#!/usr/bin/env python3
"""
hub/mcp_server.py — bridge-A MCP server (design.md §D15 bridge A, §D17;
modules.md M13e).

Single-file, Python-3-stdlib-only. Exposes six MCP tools over the shared brain,
all named with the `loreport_<verb>_<noun>` scheme (docs/visibility-design.md §4):

  - loreport_save_memory(block, provider) -> routes the block through the SAME
    scan-before-commit gate as hub/inbox_ingest.py (invoked as a subprocess —
    never re-implemented inline), committing to the calling provider's branch.
    Returns {"status": "committed" | "quarantined", "detail": "..."}.
  - loreport_read_memory(name) -> reads memories/<name>.md, knowledge/<name>.md,
    or skills/<name>/SKILL.md from the latest `main` checkout. Refused for a
    cloud-trust caller if the item's `visibility` is `local`.
  - loreport_search_memories(query) -> case-insensitive substring scan over
    INDEX.md on `main`. A cloud-trust caller never sees `local` items in the
    results.
  - loreport_load_context() -> returns hub/published/packet.md (the current
    pinned bootstrap+PROFILE+INDEX packet; already excludes `local` items).
  - loreport_view_memory_settings() -> lists every item with its visibility.
    A cloud-trust caller sees `local` items as existence-only (hidden).
  - loreport_change_memory_settings(name, visibility) -> flips one item's
    `visibility` field. A cloud-trust caller may only change items it authored.

Security invariants (§D17, §D-visibility):
  - Localhost bind ONLY — the HTTP transport binds 127.0.0.1, never the
    all-interfaces wildcard address and never an empty host string.
  - Credential -> branch mapping: each connection carries a provider identity
    (an HTTP header, or a --credential value on stdio); loreport_save_memory
    always uses THAT identity to pick the provider/* branch — a caller-supplied
    `provider` argument can never override it, so a stolen ChatGPT credential
    cannot write provider/claude or main. If the connection carries no
    recognized credential at all, loreport_save_memory fails closed
    (quarantined) rather than falling back to a caller-supplied `provider`
    argument.
  - Credential -> trust mapping: each connection also carries a trust tier
    ("local" or "cloud", from CREDENTIAL_TRUST_MAP; an unrecognized or absent
    credential defaults to "cloud" — least privilege). `local`-visibility
    items are refused to cloud-trust readers and hidden from cloud-trust
    search/settings-list results (docs/visibility-design.md §3).
  - Tools expose brain items only, never arbitrary filesystem paths.

Transports:
    python3 hub/mcp_server.py --transport stdio [--credential TOKEN]
    python3 hub/mcp_server.py --transport http  [--port 8765]
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# One credential <=> one provider/* branch. In a real deployment these tokens are
# injected by the tunnel client / connector per connection (env vars named in
# hub/config/providers.json); a connection never gets to choose its own provider
# identity.
#
# PROVIDER_BRANCHES, CREDENTIAL_PROVIDER_MAP, and CREDENTIAL_TRUST_MAP are all
# derived from hub/config/providers.json below (falling back to these same
# hardcoded values if the config is missing or unparseable, so a broken config
# file can never crash the server).
_FALLBACK_PROVIDER_BRANCHES = {
    "chatgpt": "provider/chatgpt",
    "claude": "provider/claude",
    "openclaw": "provider/openclaw",
}

_FALLBACK_CREDENTIAL_PROVIDER_MAP = {
    os.environ.get("MPB_CHATGPT_TOKEN", "chatgpt-local-dev-token"): "chatgpt",
    os.environ.get("MPB_CLAUDE_LOCAL_TOKEN", "claude-local-dev-token"): "claude",
    os.environ.get("MPB_OPENCLAW_TOKEN", "openclaw-local-dev-token"): "openclaw",
}

_FALLBACK_CREDENTIAL_TRUST_MAP = {
    os.environ.get("MPB_CHATGPT_TOKEN", "chatgpt-local-dev-token"): "cloud",
    os.environ.get("MPB_CLAUDE_LOCAL_TOKEN", "claude-local-dev-token"): "local",
    os.environ.get("MPB_OPENCLAW_TOKEN", "openclaw-local-dev-token"): "local",
}


def _load_providers_config():
    """Read hub/config/providers.json (path relative to this script's own dir) and
    build (provider_branches, credential_provider_map, credential_trust_map).

    For each entry in `credentials`, the token value is
    os.environ.get(<env_key>, <default>); if that value is non-null it is added
    to both maps (token -> provider, token -> trust). credential_provider_map
    stays a plain token->provider dict (dispatch() is unchanged this phase);
    credential_trust_map (token->trust) is exposed for a later enforcement phase.

    Falls back to the hardcoded defaults above if providers.json is missing or
    unparseable, so a broken/absent config file never crashes the server."""
    config_path = os.path.join(HERE, "config", "providers.json")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        branches = {name: info["branch"] for name, info in cfg["providers"].items()}
        cred_map = {}
        trust_map = {}
        for env_key, info in cfg["credentials"].items():
            value = os.environ.get(env_key, info.get("default"))
            if value is not None:
                cred_map[value] = info["provider"]
                trust_map[value] = info["trust"]
        return branches, cred_map, trust_map
    except (OSError, ValueError, KeyError, TypeError):
        return (
            dict(_FALLBACK_PROVIDER_BRANCHES),
            dict(_FALLBACK_CREDENTIAL_PROVIDER_MAP),
            dict(_FALLBACK_CREDENTIAL_TRUST_MAP),
        )


PROVIDER_BRANCHES, CREDENTIAL_PROVIDER_MAP, CREDENTIAL_TRUST_MAP = _load_providers_config()

TOOLS = {
    "loreport_save_memory": {
        "description": "Commit an emit-grammar v1 block through the scan-before-commit "
                       "gate to the calling provider's branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "block": {"type": "string", "description": "the emit-grammar v1 block text"},
                "provider": {"type": "string", "description": "chatgpt | claude | openclaw "
                            "(ignored if the connection already carries a credential)"},
            },
            "required": ["block"],
        },
    },
    "loreport_read_memory": {
        "description": "Read one brain item (memory, knowledge page, or skill) by name "
                       "from the latest main snapshot. A `local`-visibility item is "
                       "refused for a cloud-trust caller.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    "loreport_search_memories": {
        "description": "Case-insensitive substring search over INDEX.md on main. A "
                       "cloud-trust caller never sees `local`-visibility items in results.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    "loreport_load_context": {
        "description": "Return the current pinned publish packet (bootstrap + PROFILE + "
                       "INDEX); `local`-visibility items are already excluded.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "loreport_view_memory_settings": {
        "description": "List every brain item (memories/knowledge/skills) with its "
                       "visibility setting. A cloud-trust caller sees `local` items as "
                       "existence-only (hidden), never their type or content.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "loreport_change_memory_settings": {
        "description": "Change one item's visibility (shared|local). A cloud-trust "
                       "caller may only change items it authored (source == its own "
                       "provider); a local-trust caller may change any item.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "visibility": {"type": "string", "description": "shared | local"},
            },
            "required": ["name", "visibility"],
        },
    },
}


# --- git-from-main helpers ------------------------------------------------
#
# All reads (loreport_read_memory, loreport_search_memories,
# loreport_view_memory_settings, and the visibility check used by all of
# them) go through `git show main:<path>` / `git ls-tree main`, never the
# checked-out working tree — the shared tree can be parked on any
# provider/* branch at any moment (a capture in flight), so reading it
# directly would silently serve un-merged, un-scrubbed, possibly stale
# content (REVIEW.md #6/H1). Every one of these subprocess calls carries a
# 30s timeout (REVIEW.md #19); a stuck git process (e.g. index.lock) raises
# GitTimeout instead of hanging the request forever.

class GitTimeout(Exception):
    """Raised when a git subprocess used for a main-read exceeds its timeout."""


# Same repo-wide lock as inbox_ingest.py / brain_merge.py (identical relative
# path), so loreport_change_memory_settings' own checkout-main+edit+commit
# writes can never interleave with a capture or a merge (Phase E; the Phase C
# flock only covered inbox_ingest.py and brain_merge.py, not this tool's
# separate git-write path). Single-file/stdlib-only means this is duplicated
# rather than imported, same as SECRET_PATTERNS-style constants elsewhere.
LOCK_RELPATH = os.path.join("hub", ".loreport.lock")


@contextlib.contextmanager
def brain_lock(brain_dir):
    lock_path = os.path.join(brain_dir, LOCK_RELPATH)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _run_git(brain_dir, *args, timeout=30):
    try:
        return subprocess.run(
            ["git", "-C", brain_dir] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise GitTimeout(f"git {' '.join(args)} timed out")


def _read_from_main(brain_dir, relpath):
    """Return the content of `relpath` as committed on `main`, or None if it
    doesn't exist there. Raises GitTimeout on a stuck git process."""
    r = _run_git(brain_dir, "show", f"main:{relpath}")
    if r.returncode != 0:
        return None
    return r.stdout


def _candidate_relpaths(name):
    return [
        (f"memories/{name}.md", "memory"),
        (f"knowledge/{name}.md", "knowledge"),
        (f"skills/{name}/SKILL.md", "skill"),
    ]


def _locate_item_on_main(brain_dir, name):
    """Return (relpath, item_type, content) for the first candidate
    (memories/<name>.md | knowledge/<name>.md | skills/<name>/SKILL.md) that
    exists ON MAIN, or (None, None, None) if the name is invalid or nothing
    matches there — existence is decided by `main`, never by the working
    tree. Raises GitTimeout."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None, None, None
    for relpath, typ in _candidate_relpaths(name):
        content = _read_from_main(brain_dir, relpath)
        if content is not None:
            return relpath, typ, content
    return None, None, None


def _iter_all_items_from_main(brain_dir):
    """Yield (name, item_type, relpath) for every brain item tracked on
    `main` (memories/*.md, knowledge/*.md, skills/*/SKILL.md), enumerated
    via `git ls-tree -r --name-only main` — never a working-tree directory
    listing. Raises GitTimeout."""
    r = _run_git(brain_dir, "ls-tree", "-r", "--name-only", "main")
    if r.returncode != 0:
        return
    for relpath in sorted(r.stdout.splitlines()):
        if relpath.startswith("memories/") and relpath.endswith(".md"):
            yield os.path.splitext(os.path.basename(relpath))[0], "memory", relpath
        elif relpath.startswith("knowledge/") and relpath.endswith(".md"):
            yield os.path.splitext(os.path.basename(relpath))[0], "knowledge", relpath
        elif relpath.startswith("skills/") and relpath.endswith("/SKILL.md"):
            parts = relpath.split("/")
            if len(parts) == 3:
                yield parts[1], "skill", relpath


# --- brain item / frontmatter helpers -----------------------------------------
#
# Simple line-scan frontmatter parsing (same style as inbox_ingest.py's
# parse_simple_yaml_scalars — duplicated on purpose: every hub/*.py file is
# single-file and stdlib-only, nothing is shared by import between them).

def _parse_frontmatter_scalars(text):
    """Return the scalar key: value pairs from a leading `---`/`---` frontmatter
    block, or {} if there is no such block (or it's empty/malformed)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line or line[0] in " \t-":
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v:
            result[k] = v
    return result


def _visibility_from_text(text):
    vis = _parse_frontmatter_scalars(text).get("visibility")
    return vis if vis in ("shared", "local") else "shared"


def _item_visibility(brain_dir, name):
    """Return "local" or "shared" for the named brain item, read FROM MAIN
    (absent field defaults to "shared", per docs/visibility-design.md §1), or
    None if the item doesn't exist on main. Raises GitTimeout."""
    _relpath, _typ, content = _locate_item_on_main(brain_dir, name)
    if content is None:
        return None
    return _visibility_from_text(content)


def _set_visibility_field(text, visibility):
    """Return `text` with its frontmatter `visibility:` line set to `visibility`,
    inserting the line if absent. Leaves the rest of the file byte-for-byte
    unchanged. If `text` has no `---`/`---` frontmatter block at all (shouldn't
    happen for a well-formed item — inbox_ingest.py requires one), a minimal
    block is prepended defensively rather than corrupting the file."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return f"---\nvisibility: {visibility}\n---\n" + text
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return text  # malformed frontmatter (no closing ---); leave unchanged

    replaced = False
    new_fm_lines = []
    for line in lines[1:end_idx]:
        key = line.split(":", 1)[0].strip()
        if key == "visibility":
            new_fm_lines.append(f"visibility: {visibility}\n")
            replaced = True
        else:
            new_fm_lines.append(line)
    if not replaced:
        new_fm_lines.append(f"visibility: {visibility}\n")
    return lines[0] + "".join(new_fm_lines) + "".join(lines[end_idx:])


INDEX_ITEM_RE = re.compile(r"\[\[([^\]]+)\]\]")


# --- tool implementations -----------------------------------------------------

def tool_loreport_save_memory(brain_dir, provider, block):
    if provider not in PROVIDER_BRANCHES:
        return {"status": "quarantined", "detail": f"unknown or unauthorized provider '{provider}'"}
    fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="mpb-capture-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(block)
        script = os.path.join(HERE, "inbox_ingest.py")
        try:
            r = subprocess.run(
                [sys.executable, script, provider, tmp_path, "--brain-dir", brain_dir],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {"status": "quarantined", "detail": "git timeout"}
        detail = (r.stdout + r.stderr).strip()
        return {"status": "committed" if r.returncode == 0 else "quarantined", "detail": detail}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def tool_loreport_read_memory(brain_dir, name, trust):
    if not name or "/" in name or "\\" in name or ".." in name:
        return {"error": "invalid item name"}
    try:
        _relpath, _typ, content = _locate_item_on_main(brain_dir, name)
        if content is None:
            return {"error": "not found"}
        vis = _visibility_from_text(content)
    except GitTimeout:
        return {"error": "git timeout"}
    if vis == "local" and trust != "local":
        return {"error": "local item — not available to this caller"}
    return {"content": content}


def tool_loreport_search_memories(brain_dir, query, trust):
    try:
        index_content = _read_from_main(brain_dir, "INDEX.md")
        if index_content is None:
            return {"error": "INDEX.md not found on main"}
        q = (query or "").lower()
        matches = [line for line in index_content.splitlines() if q in line.lower()]
        if trust != "local":
            filtered = []
            for line in matches:
                m = INDEX_ITEM_RE.search(line)
                if m and _item_visibility(brain_dir, m.group(1)) == "local":
                    continue
                filtered.append(line)
            matches = filtered
    except GitTimeout:
        return {"error": "git timeout"}
    return {"matches": matches}


def tool_loreport_load_context(brain_dir):
    packet_path = os.path.join(brain_dir, "hub", "published", "packet.md")
    if not os.path.isfile(packet_path):
        return {"error": "no published packet yet — run snapshot_publish.py"}
    with open(packet_path, "r", encoding="utf-8") as fh:
        return {"content": fh.read()}


def tool_loreport_view_memory_settings(brain_dir, trust):
    items = []
    try:
        for name, typ, relpath in _iter_all_items_from_main(brain_dir):
            content = _read_from_main(brain_dir, relpath)
            if content is None:
                continue  # raced with a concurrent write between ls-tree and show; skip
            vis = _visibility_from_text(content)
            if vis == "local" and trust != "local":
                items.append({"name": name, "visibility": "local", "hidden": True})
            else:
                items.append({"name": name, "type": typ, "visibility": vis})
    except GitTimeout:
        return {"error": "git timeout"}
    return {"items": items}


def tool_loreport_change_memory_settings(brain_dir, name, visibility, trust, provider):
    if visibility not in ("shared", "local"):
        return {"error": "visibility must be 'shared' or 'local'"}
    try:
        relpath, _typ, text = _locate_item_on_main(brain_dir, name)
    except GitTimeout:
        return {"status": "quarantined", "detail": "git timeout"}
    if relpath is None:
        return {"error": "not found"}
    source = _parse_frontmatter_scalars(text).get("source")

    if trust == "local":
        pass  # a local-trust caller may change any item
    elif trust == "cloud" and provider is not None and source == provider:
        pass  # a cloud-trust caller may change only the items it authored
    else:
        return {"error": "not permitted: a cloud caller may only change memories it authored"}

    new_text = _set_visibility_field(text, visibility)
    path = os.path.join(brain_dir, relpath)

    try:
        # The checkout-main+edit+commit sequence is its own git write against
        # the shared working tree — same exclusive lock as inbox_ingest.py's
        # capture and brain_merge.py's merge, so none of the three can ever
        # interleave (Phase E; this tool previously did its git write
        # unlocked).
        with brain_lock(brain_dir):
            _run_git(brain_dir, "checkout", "main")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            _run_git(brain_dir, "add", relpath)
            actor = provider if provider else "local"
            _run_git(brain_dir, "commit", "-m", f"brain(settings): {name} visibility -> {visibility} via {actor}")
    except GitTimeout:
        return {"status": "quarantined", "detail": "git timeout"}

    return {"status": "changed", "name": name, "visibility": visibility}


def dispatch(brain_dir, credential, name, arguments):
    arguments = arguments or {}
    provider_from_credential = CREDENTIAL_PROVIDER_MAP.get(credential) if credential else None
    trust = CREDENTIAL_TRUST_MAP.get(credential, "cloud") if credential else "cloud"

    if name == "loreport_save_memory":
        # The credential's mapped provider always wins over any caller-supplied
        # `provider` argument — a stolen credential cannot pick another branch.
        # If there is no recognized credential at all, fail closed: never fall
        # back to a caller-supplied `provider` argument for routing.
        if provider_from_credential is None:
            return {"status": "quarantined",
                    "detail": "no recognized credential; refusing to route capture"}
        return tool_loreport_save_memory(brain_dir, provider_from_credential, arguments.get("block", ""))
    if name == "loreport_read_memory":
        return tool_loreport_read_memory(brain_dir, arguments.get("name", ""), trust)
    if name == "loreport_search_memories":
        return tool_loreport_search_memories(brain_dir, arguments.get("query", ""), trust)
    if name == "loreport_load_context":
        return tool_loreport_load_context(brain_dir)
    if name == "loreport_view_memory_settings":
        return tool_loreport_view_memory_settings(brain_dir, trust)
    if name == "loreport_change_memory_settings":
        return tool_loreport_change_memory_settings(
            brain_dir, arguments.get("name", ""), arguments.get("visibility", ""),
            trust, provider_from_credential)
    return {"error": f"unknown tool '{name}'"}


# --- JSON-RPC 2.0 / MCP framing -----------------------------------------------

def handle_request(brain_dir, credential, req):
    # JSON-RPC 2.0 notifications (no "id" key) MUST NOT get a response — e.g.
    # MCP's "notifications/initialized". Replying anyway desyncs the client's
    # request/response pairing (it starts matching the wrong response to the
    # wrong pending request), which reads as "invalid request" / a dead
    # session on the tunnel-client side even though the server is fine. This
    # check must apply to every transport (stdio AND http), not just stdio.
    if "id" not in req:
        return None

    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "loreport-hub", "version": "1.0"},
            "capabilities": {"tools": {}},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
                for n, t in TOOLS.items()
            ]
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        raw = dispatch(brain_dir, credential, tool_name, params.get("arguments"))
        result = _as_tool_result(raw)
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}}

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _as_tool_result(raw):
    """Wrap a tool's return dict in the MCP tools/call result shape: a `content`
    array of typed blocks (+ isError). MCP clients (e.g. the ChatGPT connector)
    reject a bare dict — `result.content` MUST be an array of {type,text} blocks.
    Without this, brain_read/brain_surface returned `{"content": "<str>"}` (content
    as a string, not an array) and failed the client's schema validation, so the
    model got nothing back even though the server read the item correctly."""
    is_error = isinstance(raw, dict) and "error" in raw
    if isinstance(raw, dict) and isinstance(raw.get("content"), str):
        text = raw["content"]
    elif isinstance(raw, dict) and "matches" in raw:
        matches = raw.get("matches") or []
        text = "\n".join(matches) if matches else "(no matches)"
    else:
        text = json.dumps(raw)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _write_response(payload):
    """Write one JSON-RPC response line. Returns False if the pipe is gone (the
    caller should stop reading stdin) instead of letting BrokenPipeError/OSError
    crash the process — a torn-down pipe from the host's side is normal during
    reconnects and must not kill the whole stdio session."""
    try:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def run_stdio(brain_dir, credential):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            if not _write_response({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}):
                break
            continue
        resp = handle_request(brain_dir, credential, req)
        if resp is None:
            continue
        if not _write_response(resp):
            break


def make_http_handler(brain_dir):
    class MCPHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                req = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            # Per-connection credential identifies exactly one provider — the
            # tunnel/connector sets this header per authenticated connection.
            credential = self.headers.get("X-MPB-Credential")
            resp = handle_request(brain_dir, credential, req)
            if resp is None:
                # A JSON-RPC notification (no "id") gets no body — only an
                # Accepted status, same "no response" semantics as stdio.
                self.send_response(202)
                self.end_headers()
                return
            body = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass  # quiet; the daily digest is the audit surface, not stderr noise

    return MCPHandler


def run_http(brain_dir, host, port):
    # SECURITY: bind localhost ONLY. Never the all-interfaces wildcard address,
    # never an empty host string. The tunnel client is the only thing allowed to
    # reach this port from outside the machine (hub/config/tunnel-client.json).
    if host != "127.0.0.1":
        raise ValueError("refusing to bind to a non-localhost address")
    handler_cls = make_http_handler(brain_dir)
    server = HTTPServer((host, port), handler_cls)
    print(f"hub mcp_server listening on http://{host}:{port} (localhost only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


# --- CLI -----------------------------------------------------------------

def default_brain_dir():
    return REPO_ROOT


def main():
    parser = argparse.ArgumentParser(description="Loreport hub MCP server (bridge A).")
    parser.add_argument("--brain-dir", default=None,
                        help="Brain repo root (default: inferred from this script's location)")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--port", type=int, default=8765, help="HTTP transport port (localhost only)")
    parser.add_argument("--credential", default=None,
                        help="stdio-mode credential identifying the calling provider")
    args = parser.parse_args()

    brain_dir = args.brain_dir or default_brain_dir()

    if args.transport == "stdio":
        run_stdio(brain_dir, args.credential)
    else:
        run_http(brain_dir, "127.0.0.1", args.port)


if __name__ == "__main__":
    main()
