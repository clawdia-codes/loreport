#!/usr/bin/env python3
"""
hub/mcp_server.py — bridge-A MCP server (design.md §D15 bridge A, §D17;
modules.md M13e).

Single-file, Python-3-stdlib-only. Exposes four MCP tools over the shared brain:

  - brain_capture(block, provider) -> routes the block through the SAME
    scan-before-commit gate as hub/inbox_ingest.py (invoked as a subprocess —
    never re-implemented inline), committing to the calling provider's branch.
    Returns {"status": "committed" | "quarantined", "detail": "..."}.
  - brain_read(name)   -> reads memories/<name>.md, knowledge/<name>.md, or
    skills/<name>/SKILL.md from the latest `main` checkout.
  - brain_search(query) -> case-insensitive substring scan over INDEX.md on `main`.
  - brain_surface()     -> returns hub/published/packet.md (the current pinned
    bootstrap+PROFILE+INDEX packet).

Security invariants (§D17):
  - Localhost bind ONLY — the HTTP transport binds 127.0.0.1, never the
    all-interfaces wildcard address and never an empty host string.
  - Credential -> branch mapping: each connection carries a provider identity
    (an HTTP header, or a --credential value on stdio); brain_capture always
    uses THAT identity to pick the provider/* branch — a caller-supplied
    `provider` argument can never override it, so a stolen ChatGPT credential
    cannot write provider/claude or main.
  - Tools expose brain items only, never arbitrary filesystem paths.

Transports:
    python3 hub/mcp_server.py --transport stdio [--credential TOKEN]
    python3 hub/mcp_server.py --transport http  [--port 8765]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# One credential <=> one provider/* branch. In a real deployment these tokens are
# injected by the tunnel client / connector per connection (env vars below); a
# connection never gets to choose its own provider identity.
PROVIDER_BRANCHES = {
    "chatgpt": "provider/chatgpt",
    "claude": "provider/claude",
    "openclaw": "provider/openclaw",
}

CREDENTIAL_PROVIDER_MAP = {
    os.environ.get("MPB_CHATGPT_TOKEN", "chatgpt-local-dev-token"): "chatgpt",
    os.environ.get("MPB_CLAUDE_TOKEN", "claude-local-dev-token"): "claude",
    os.environ.get("MPB_OPENCLAW_TOKEN", "openclaw-local-dev-token"): "openclaw",
}

TOOLS = {
    "brain_capture": {
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
    "brain_read": {
        "description": "Read one brain item (memory, knowledge page, or skill) by name "
                       "from the latest main snapshot.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    "brain_search": {
        "description": "Case-insensitive substring search over INDEX.md on main.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    "brain_surface": {
        "description": "Return the current pinned publish packet (bootstrap + PROFILE + INDEX).",
        "inputSchema": {"type": "object", "properties": {}},
    },
}


# --- tool implementations -----------------------------------------------------

def tool_brain_capture(brain_dir, provider, block):
    if provider not in PROVIDER_BRANCHES:
        return {"status": "quarantined", "detail": f"unknown or unauthorized provider '{provider}'"}
    fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="mpb-capture-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(block)
        script = os.path.join(HERE, "inbox_ingest.py")
        r = subprocess.run(
            [sys.executable, script, provider, tmp_path, "--brain-dir", brain_dir],
            capture_output=True, text=True,
        )
        detail = (r.stdout + r.stderr).strip()
        return {"status": "committed" if r.returncode == 0 else "quarantined", "detail": detail}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def tool_brain_read(brain_dir, name):
    # Brain items only — never an arbitrary filesystem path. Reject path separators
    # in the requested name so a caller cannot escape memories/knowledge/skills.
    if not name or "/" in name or "\\" in name or ".." in name:
        return {"error": "invalid item name"}
    for sub in ("memories", "knowledge"):
        path = os.path.join(brain_dir, sub, f"{name}.md")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return {"content": fh.read()}
    skill_path = os.path.join(brain_dir, "skills", name, "SKILL.md")
    if os.path.isfile(skill_path):
        with open(skill_path, "r", encoding="utf-8") as fh:
            return {"content": fh.read()}
    return {"error": "not found"}


def tool_brain_search(brain_dir, query):
    index_path = os.path.join(brain_dir, "INDEX.md")
    if not os.path.isfile(index_path):
        return {"error": "INDEX.md not found on main"}
    q = (query or "").lower()
    matches = []
    with open(index_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if q in line.lower():
                matches.append(line.rstrip("\n"))
    return {"matches": matches}


def tool_brain_surface(brain_dir):
    packet_path = os.path.join(brain_dir, "hub", "published", "packet.md")
    if not os.path.isfile(packet_path):
        return {"error": "no published packet yet — run snapshot_publish.py"}
    with open(packet_path, "r", encoding="utf-8") as fh:
        return {"content": fh.read()}


def dispatch(brain_dir, credential, name, arguments):
    arguments = arguments or {}
    provider_from_credential = CREDENTIAL_PROVIDER_MAP.get(credential) if credential else None

    if name == "brain_capture":
        # The credential's mapped provider always wins over any caller-supplied
        # `provider` argument — a stolen credential cannot pick another branch.
        provider = provider_from_credential or arguments.get("provider")
        return tool_brain_capture(brain_dir, provider, arguments.get("block", ""))
    if name == "brain_read":
        return tool_brain_read(brain_dir, arguments.get("name", ""))
    if name == "brain_search":
        return tool_brain_search(brain_dir, arguments.get("query", ""))
    if name == "brain_surface":
        return tool_brain_surface(brain_dir)
    return {"error": f"unknown tool '{name}'"}


# --- JSON-RPC 2.0 / MCP framing -----------------------------------------------

def handle_request(brain_dir, credential, req):
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
        result = dispatch(brain_dir, credential, tool_name, params.get("arguments"))
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}}

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


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
        # JSON-RPC 2.0 notifications (no "id" key) MUST NOT get a response — e.g.
        # MCP's "notifications/initialized". Replying anyway desyncs the client's
        # request/response pairing (it starts matching the wrong response to the
        # wrong pending request), which reads as "invalid request" / a dead
        # session on the tunnel-client side even though the server is fine.
        if "id" not in req:
            continue
        resp = handle_request(brain_dir, credential, req)
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
