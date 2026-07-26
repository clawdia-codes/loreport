#!/usr/bin/env python3
"""
hub/report_build.py — self-contained HTML report of a Loreport brain
(design.md §D16 sibling; see hub/snapshot_publish.py for the packet builder
this shares conventions with).

Single-file, Python-3-stdlib-only. Reads EVERYTHING from `main` via
`git show main:<path>` / `git ls-tree main` — NEVER the working tree, which
the shared repo can have parked on any provider/* branch at any moment
(mid-capture, or between merges). Refuses to run if `main` cannot be
resolved.

Produces ONE offline-capable HTML file: no CDN, no web fonts, no remote
images, no external requests of any kind. All CSS/JS inline. The page is a
dashboard — status counts, a per-provider activity table, a client-side
search box, then every memory/knowledge/skill entry as a collapsible card
with a clear privacy badge. This report includes PRIVATE entries; it is
meant to be served only on the user's private tailnet, never shared as-is.

CLI:
    python3 hub/report_build.py --brain-dir PATH [--out PATH]

Default --out: <brain-dir>/hub/published/report.html
"""

import argparse
import html
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

# --- git access (read-only, always from `main`) -----------------------------


def _run_git(brain_dir, *args, timeout=30):
    try:
        return subprocess.run(
            ["git", "-C", brain_dir] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"error: git {' '.join(args)} timed out", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("error: git executable not found", file=sys.stderr)
        sys.exit(1)


def resolve_main(brain_dir):
    """Return `main`'s (full_sha, short_sha). Refuse to run if `main` cannot
    be resolved — never silently fall back to the working tree or HEAD."""
    r = _run_git(brain_dir, "rev-parse", "main")
    if r.returncode != 0:
        print(
            f"error: cannot resolve 'main' in brain repo at {brain_dir!r} — "
            "refusing to build a report from an unknown ref",
            file=sys.stderr,
        )
        sys.exit(1)
    full = r.stdout.strip()
    r2 = _run_git(brain_dir, "rev-parse", "--short", "main")
    short = r2.stdout.strip() if r2.returncode == 0 else full[:7]
    return full, short


def read_from_main(brain_dir, relpath):
    """Return `relpath`'s content as committed on `main`, or None if it
    doesn't exist there."""
    r = _run_git(brain_dir, "show", f"main:{relpath}")
    if r.returncode != 0:
        return None
    return r.stdout


def ls_tree(brain_dir, prefix):
    """List every path on `main` under `prefix` (recursive), or [] if the
    prefix doesn't exist (e.g. an empty/absent knowledge/ dir)."""
    r = _run_git(brain_dir, "ls-tree", "-r", "--name-only", "main", "--", prefix)
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


# --- frontmatter + fail-closed visibility -----------------------------------
#
# Duplicated line-scan frontmatter parsing (same style as brain_merge.py /
# snapshot_publish.py / mcp_server.py — every hub/*.py file is single-file
# and stdlib-only, nothing is shared by import between them).


def parse_frontmatter(text):
    """Return (fm_dict, body_text). fm_dict is {} if there's no well-formed
    leading `---` ... `---` block."""
    if text is None:
        return {}, ""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    fm = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        key, sep, value = line.partition(":")
        if sep:
            v = value.split("#", 1)[0].strip()
            if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
                v = v[1:-1]
            fm[key.strip().lower()] = v
        i += 1
    body = "".join(lines[i + 1:]) if i < len(lines) else ""
    return fm, body


def visibility_from_text(text):
    """Return "shared" or "private" for an item's raw text on `main`.

    Ported from hub/snapshot_publish.py's `_visibility_from_text` (same
    fail-closed rule, duplicated here on purpose per the no-cross-import
    convention). FAIL CLOSED: anything we cannot parse as a well-formed
    frontmatter block is treated as private. Within a well-formed block, a
    missing `visibility:` key defaults to shared (the documented frontmatter
    contract); an explicit non-"shared" value (e.g. "local") is private. A
    false "private" merely hides an item from this report's shareable count
    — visible and recoverable. A false "shared" would be a leak.
    """
    if text is None:
        return "private"
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "private"
    seen = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if not sep or key.strip().lower() != "visibility":
            continue
        seen = value.split("#", 1)[0].strip().strip('"').strip("'").strip().lower()
    if seen is None:
        return "shared"
    return "shared" if seen == "shared" else "private"


# --- providers ---------------------------------------------------------------


def load_providers(brain_dir, script_dir):
    """Return {provider_name: trust} where trust is "private" or "cloud".

    Primary source: hub/config/providers.json in the FRAMEWORK repo, located
    at <brain-dir>/../loreport/hub/config/providers.json. Falls back to a
    providers.json next to this script (in case the framework repo isn't
    literally named "loreport" next to the brain), then to deriving provider
    names from provider/* branches (trust unknown -> treated as private,
    the safe default) if no config can be found at all.
    """
    candidates = [
        os.path.normpath(os.path.join(brain_dir, "..", "loreport", "hub", "config", "providers.json")),
        os.path.normpath(os.path.join(script_dir, "config", "providers.json")),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        providers = data.get("providers", {}) or {}
        creds = data.get("credentials", {}) or {}
        if not providers:
            continue
        result = {}
        for name, meta in providers.items():
            trusts = {c.get("trust") for c in creds.values() if c.get("provider") == name}
            result[name] = "cloud" if "cloud" in trusts else "private"
        return result

    # Fallback: derive from provider/* branches (local + remote).
    r = _run_git(brain_dir, "branch", "-a", "--format=%(refname:short)")
    names = []
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            line = line.strip()
            for pre in ("provider/", "remotes/origin/provider/"):
                if line.startswith(pre):
                    n = line[len(pre):]
                    if n not in names:
                        names.append(n)
    return {n: "private" for n in names}


CAPTURE_RE = re.compile(
    r"^brain\(capture\):\s+.+?\s+via\s+([\w.-]+)\s+\[inbox\]\s*$"
)


def load_provider_activity(brain_dir, provider_names):
    """Derive {provider: {"count", "first_iso", "last_iso"}} from commits on
    `main` whose subject matches `brain(capture): <name> via <provider>
    [inbox]`. Providers with zero matching commits still get an entry (count
    0, first/last None) — a silent provider must still show up as 0/never,
    never be dropped from the table."""
    activity = {p: {"count": 0, "first_iso": None, "last_iso": None} for p in provider_names}
    r = _run_git(brain_dir, "log", "main", "--format=%H%x1f%aI%x1f%s")
    if r.returncode != 0:
        return activity
    for line in r.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        _sha, iso_date, subject = parts
        m = CAPTURE_RE.match(subject.strip())
        if not m:
            continue
        provider = m.group(1)
        a = activity.setdefault(provider, {"count": 0, "first_iso": None, "last_iso": None})
        a["count"] += 1
        if a["first_iso"] is None or iso_date < a["first_iso"]:
            a["first_iso"] = iso_date
        if a["last_iso"] is None or iso_date > a["last_iso"]:
            a["last_iso"] = iso_date
    return activity


def relative_time(iso_str, now):
    if not iso_str:
        return "never"
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max((now - dt).total_seconds(), 0)
    mins, hours, days = secs / 60, secs / 3600, secs / 86400
    if secs < 90:
        return "just now"
    if mins < 60:
        return f"{int(mins)}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    if days < 30:
        return f"{int(days)}d ago"
    months = days / 30.44
    if months < 12:
        return f"{int(months)}mo ago"
    return f"{int(days / 365.25)}y ago"


def short_date(iso_str):
    if not iso_str:
        return "never"
    try:
        return datetime.fromisoformat(iso_str).date().isoformat()
    except ValueError:
        return "unknown"


# --- entries ------------------------------------------------------------------


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "entry"


def norm_key(name):
    return re.sub(r"[-_\s]+", "", name.lower())


def load_entries(brain_dir):
    entries = []
    for prefix, kind in (("memories/", "memory"), ("knowledge/", "knowledge")):
        for path in sorted(ls_tree(brain_dir, prefix)):
            if not path.endswith(".md"):
                continue
            text = read_from_main(brain_dir, path)
            if text is None:
                continue
            fm, body = parse_frontmatter(text)
            stem = os.path.splitext(os.path.basename(path))[0]
            entries.append({
                "path": path,
                "kind": kind,
                "name": fm.get("name") or stem,
                "description": fm.get("description", ""),
                "type": fm.get("type", "—"),
                "source": fm.get("source", "—"),
                "captured": fm.get("captured", ""),
                "shared": visibility_from_text(text) == "shared",
                "body": body,
            })

    for path in sorted(ls_tree(brain_dir, "skills/")):
        if not path.endswith("/SKILL.md"):
            continue
        text = read_from_main(brain_dir, path)
        if text is None:
            continue
        fm, body = parse_frontmatter(text)
        parts = path.split("/")
        dirname = parts[1] if len(parts) > 1 else path
        entries.append({
            "path": path,
            "kind": "skill",
            "name": fm.get("name") or dirname,
            "description": fm.get("description", ""),
            "type": "skill",
            "source": fm.get("source", "—"),
            "captured": fm.get("captured", ""),
            "shared": visibility_from_text(text) == "shared",
            "body": body,
        })

    # Assign unique slugs (for in-page anchors) before sorting so wikilink
    # resolution is stable regardless of display order.
    used = {}
    for e in entries:
        base = slugify(e["name"])
        n = used.get(base, 0)
        used[base] = n + 1
        e["slug"] = base if n == 0 else f"{base}-{n}"

    entries.sort(key=lambda e: (e["captured"] or "0000-00-00", e["name"]), reverse=True)
    return entries


# --- tiny markdown-ish renderer (escape-first, safe by construction) --------
#
# Everything is html.escape()'d before any tag is added, so user-controlled
# entry bodies can never introduce a live `<` / `>` / `&`; the only real
# angle brackets in the output are the ones this renderer writes itself.

CODE_SPAN_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_RE = re.compile(r"^[-*]\s+(.*)$")
ALLOWED_SCHEMES = ("http://", "https://", "mailto:")


def render_inline(text, slug_lookup):
    esc = html.escape(text, quote=True)
    esc = CODE_SPAN_RE.sub(lambda m: f"<code>{m.group(1)}</code>", esc)
    esc = BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", esc)

    def link_sub(m):
        label, url = m.group(1), m.group(2)
        if url.startswith(ALLOWED_SCHEMES):
            return f'<a href="{url}" rel="noopener noreferrer">{label}</a>'
        return f"{label} ({url})"

    esc = LINK_RE.sub(link_sub, esc)

    def wiki_sub(m):
        target = m.group(1)
        slug = slug_lookup.get(norm_key(target))
        if slug:
            return f'<a class="wikilink" href="#entry-{slug}">{target}</a>'
        return f'<span class="wikilink wikilink-broken" title="No matching entry in this brain">{target}</span>'

    esc = WIKILINK_RE.sub(wiki_sub, esc)
    return esc


def render_body(text, slug_lookup):
    lines = text.split("\n")
    out = []
    para_buf, list_buf = [], []

    def flush_para():
        if para_buf:
            out.append("<p>" + render_inline(" ".join(para_buf), slug_lookup) + "</p>")
            para_buf.clear()

    def flush_list():
        if list_buf:
            items = "".join(f"<li>{render_inline(it, slug_lookup)}</li>" for it in list_buf)
            out.append(f"<ul>{items}</ul>")
            list_buf.clear()

    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            flush_para(); flush_list()
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence (or EOF if unterminated)
            out.append(f'<pre class="codeblock"><code>{html.escape(chr(10).join(code_lines))}</code></pre>')
            continue
        m = HEADING_RE.match(stripped)
        if m:
            flush_para(); flush_list()
            level = min(len(m.group(1)) + 3, 6)
            out.append(f"<h{level}>{render_inline(m.group(2), slug_lookup)}</h{level}>")
            i += 1
            continue
        m = LIST_RE.match(stripped)
        if m:
            flush_para()
            list_buf.append(m.group(1))
            i += 1
            continue
        if not stripped:
            flush_para(); flush_list()
            i += 1
            continue
        flush_list()
        para_buf.append(stripped)
        i += 1
    flush_para(); flush_list()
    return "\n".join(out)


# --- page assembly ------------------------------------------------------------

CSS = """
:root {
  color-scheme: light dark;
  --bg: #f5f4f1; --panel: #ffffff; --ink: #1c1a17; --muted: #6b6558;
  --line: #e4e0d8; --accent: #7a5c3e; --accent-ink: #ffffff;
  --shared-bg: #e4efe2; --shared-ink: #285c34; --shared-line: #b9dcb8;
  --private-bg: #f6e7dd; --private-ink: #8a3f1a; --private-line: #eec6a9;
  --banner-bg: #fdf1e0; --banner-line: #e8c98f; --banner-ink: #6b4a15;
  --code-bg: #f0eee8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161513; --panel: #201e1b; --ink: #ece8e0; --muted: #a39d8e;
    --line: #35322c; --accent: #d8a76b; --accent-ink: #201e1b;
    --shared-bg: #1d2b1f; --shared-ink: #9adba0; --shared-line: #345a3a;
    --private-bg: #322219; --private-ink: #e8a97b; --private-line: #6b432a;
    --banner-bg: #2c2413; --banner-line: #6b5327; --banner-ink: #e8c98f;
    --code-bg: #262420;
  }
}
:root[data-theme="dark"] {
  --bg: #161513; --panel: #201e1b; --ink: #ece8e0; --muted: #a39d8e;
  --line: #35322c; --accent: #d8a76b; --accent-ink: #201e1b;
  --shared-bg: #1d2b1f; --shared-ink: #9adba0; --shared-line: #345a3a;
  --private-bg: #322219; --private-ink: #e8a97b; --private-line: #6b432a;
  --banner-bg: #2c2413; --banner-line: #6b5327; --banner-ink: #e8c98f;
  --code-bg: #262420;
}
:root[data-theme="light"] {
  --bg: #f5f4f1; --panel: #ffffff; --ink: #1c1a17; --muted: #6b6558;
  --line: #e4e0d8; --accent: #7a5c3e; --accent-ink: #ffffff;
  --shared-bg: #e4efe2; --shared-ink: #285c34; --shared-line: #b9dcb8;
  --private-bg: #f6e7dd; --private-ink: #8a3f1a; --private-line: #eec6a9;
  --banner-bg: #fdf1e0; --banner-line: #e8c98f; --banner-ink: #6b4a15;
  --code-bg: #f0eee8;
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0; max-width: 100%; overflow-x: hidden;
  background: var(--bg); color: var(--ink);
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 720px; margin: 0 auto; padding: 1rem 1rem 4rem; }
header.top h1 { font-size: 1.5rem; margin: 0 0 0.15rem; letter-spacing: -0.01em; }
header.top .sub { color: var(--muted); font-size: 0.85rem; margin: 0; }
.banner {
  background: var(--banner-bg); border: 1px solid var(--banner-line);
  color: var(--banner-ink); border-radius: 10px; padding: 0.75rem 1rem;
  margin: 1rem 0; font-size: 0.88rem; line-height: 1.45;
}
.banner strong { font-weight: 700; }
section { margin: 1.75rem 0; }
h2.section-title {
  font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--muted); margin: 0 0 0.6rem; font-weight: 700;
}
.status-line { font-size: 1.05rem; margin: 0.15rem 0; }
.status-line.sub { color: var(--muted); font-size: 0.92rem; }
.table-scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; white-space: nowrap; }
th, td { text-align: left; padding: 0.55rem 0.7rem; border-bottom: 1px solid var(--line); }
tr:last-child td { border-bottom: none; }
th { color: var(--muted); font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }
.trust-pill { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px; font-size: 0.75rem; }
.trust-private { background: var(--shared-bg); color: var(--shared-ink); }
.trust-cloud { background: var(--private-bg); color: var(--private-ink); }
input#search {
  width: 100%; padding: 0.7rem 0.9rem; font-size: 1rem; border-radius: 10px;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink);
}
input#search:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
#search-count { color: var(--muted); font-size: 0.8rem; margin: 0.4rem 0 0; min-height: 1em; }
details.entry {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  margin-bottom: 0.6rem; padding: 0.15rem 0.9rem;
}
details.entry.is-hidden { display: none; }
details.entry summary {
  cursor: pointer; padding: 0.7rem 0; list-style: none;
  display: flex; align-items: baseline; gap: 0.6rem; flex-wrap: wrap;
}
details.entry summary::-webkit-details-marker { display: none; }
details.entry summary::before {
  content: "\\25B8"; color: var(--muted); font-size: 0.75rem; margin-right: 0.1rem;
  transition: transform 0.15s ease;
}
details.entry[open] summary::before { transform: rotate(90deg); }
h3.entry-name { font-size: 1rem; margin: 0; font-weight: 650; flex: 1 1 auto; min-width: 0; }
.badge { font-size: 0.7rem; padding: 0.12rem 0.55rem; border-radius: 999px; white-space: nowrap; border: 1px solid transparent; }
.badge-shared { background: var(--shared-bg); color: var(--shared-ink); border-color: var(--shared-line); }
.badge-private { background: var(--private-bg); color: var(--private-ink); border-color: var(--private-line); }
.entry-meta { color: var(--muted); font-size: 0.78rem; margin: -0.3rem 0 0.5rem; }
.entry-desc { color: var(--ink); font-size: 0.92rem; margin: 0 0 0.6rem; opacity: 0.92; }
.entry-body { border-top: 1px dashed var(--line); padding: 0.8rem 0 1rem; font-size: 0.92rem; line-height: 1.55; }
.entry-body h4, .entry-body h5, .entry-body h6 { font-size: 0.95rem; margin: 1em 0 0.35em; }
.entry-body p { margin: 0 0 0.7em; overflow-wrap: anywhere; }
.entry-body ul { margin: 0 0 0.7em; padding-left: 1.2em; }
.entry-body li { margin: 0.2em 0; }
.entry-body code { background: var(--code-bg); padding: 0.1em 0.35em; border-radius: 4px; font-size: 0.87em; }
.entry-body pre.codeblock {
  background: var(--code-bg); border-radius: 8px; padding: 0.7rem 0.85rem;
  overflow-x: auto; margin: 0 0 0.8em; max-width: 100%;
}
.entry-body pre.codeblock code { background: none; padding: 0; }
a.wikilink { color: var(--accent); text-decoration: none; border-bottom: 1px solid var(--accent); }
.wikilink-broken { color: var(--muted); border-bottom: 1px dashed var(--muted); }
footer.report-footer { color: var(--muted); font-size: 0.75rem; text-align: center; margin-top: 2rem; }
"""

JS = """
(function () {
  var input = document.getElementById('search');
  var countEl = document.getElementById('search-count');
  var entries = Array.prototype.slice.call(document.querySelectorAll('.entry'));
  if (!input) return;
  input.addEventListener('input', function () {
    var term = input.value.trim().toLowerCase();
    var shown = 0;
    for (var i = 0; i < entries.length; i++) {
      var el = entries[i];
      var hay = el.getAttribute('data-search') || '';
      var match = term === '' || hay.indexOf(term) !== -1;
      if (match) shown++;
      el.classList.toggle('is-hidden', !match);
    }
    countEl.textContent = term ? (shown + ' of ' + entries.length + ' entries') : '';
  });
})();
"""


def brain_committed_at(brain_dir):
    """(timestamp, short sha) of the last commit that changed the user's ENTRIES.

    Falls back to the epoch rather than to `now` if git cannot answer: a wrong-but-
    stable timestamp keeps the output deterministic, whereas falling back to the
    clock would silently reintroduce the daily churn this exists to prevent."""
    # Restrict to CONTENT paths. Deriving this from main's last commit of ANY path
    # creates a feedback loop: every sync commits the rebuilt INDEX and packet, which
    # moves main, which changes this stamp, which rewrites the report, which commits
    # again -- one 800KB commit per sync forever, with nothing having changed. Only a
    # real change to the user's entries should move it.
    r = _run_git(brain_dir, "log", "-1", "--format=%cI%x09%h", "main", "--",
                 "memories", "knowledge", "skills", "PROFILE.md")
    if r.returncode == 0 and r.stdout.strip():
        iso, _, sha = r.stdout.strip().partition("\t")
        try:
            return datetime.fromisoformat(iso).astimezone(timezone.utc), sha
        except ValueError:
            pass
    return datetime.fromtimestamp(0, timezone.utc), "unknown"


def build_html(version, main_short_sha, providers, activity, entries, now):
    generated = now.strftime("%Y-%m-%d %H:%M UTC")  # brain's last commit, not build time

    memories_n = sum(1 for e in entries if e["kind"] == "memory")
    knowledge_n = sum(1 for e in entries if e["kind"] == "knowledge")
    skills_n = sum(1 for e in entries if e["kind"] == "skill")
    corpus = [e for e in entries if e["kind"] in ("memory", "knowledge")]
    shareable_n = sum(1 for e in corpus if e["shared"])
    private_n = sum(1 for e in corpus if not e["shared"])

    slug_lookup = {norm_key(e["name"]): e["slug"] for e in entries}

    parts = []
    parts.append("<!doctype html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>Loreport &middot; {html.escape(version)}</title>")
    parts.append(f"<style>{CSS}</style>")
    parts.append("</head>")
    parts.append("<body>")
    parts.append('<div class="wrap">')

    # Header
    parts.append('<header class="top">')
    parts.append(f"<h1>Loreport <span style=\"color:var(--muted);font-weight:400;\">v{html.escape(version)}</span></h1>")
    parts.append(
        f'<p class="sub">As of {html.escape(generated)} &middot; entries@'
        f'<code>{html.escape(main_short_sha)}</code></p>'
    )
    parts.append("</header>")

    # Privacy banner
    parts.append(
        '<div class="banner"><strong>Private view.</strong> This report includes every '
        'entry in your brain, including ones marked private that never leave this '
        'machine. It is served only on your own private tailnet &mdash; treat this '
        'page itself as private, and don’t share this link.</div>'
    )

    # Status
    parts.append("<section>")
    parts.append('<h2 class="section-title">Status</h2>')
    parts.append(
        f'<p class="status-line">{memories_n} memories &middot; {skills_n} skills &middot; '
        f'{knowledge_n} knowledge pages</p>'
    )
    parts.append(
        f'<p class="status-line sub">{shareable_n} shareable &middot; {private_n} stay on this machine</p>'
    )
    parts.append("</section>")

    # Providers
    parts.append("<section>")
    parts.append('<h2 class="section-title">Providers</h2>')
    parts.append('<div class="table-scroll"><table>')
    parts.append(
        "<tr><th>Provider</th><th>Trust</th><th>First seen</th>"
        "<th>Last contribution</th><th>Entries</th></tr>"
    )
    for name, trust in providers.items():
        a = activity.get(name, {"count": 0, "first_iso": None, "last_iso": None})
        trust_class = "trust-cloud" if trust == "cloud" else "trust-private"
        trust_label = "cloud" if trust == "cloud" else "private"
        parts.append(
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f'<td><span class="trust-pill {trust_class}">{trust_label}</span></td>'
            f"<td>{html.escape(short_date(a['first_iso']))}</td>"
            f"<td>{html.escape(relative_time(a['last_iso'], now))}</td>"
            f"<td>{a['count']}</td>"
            "</tr>"
        )
    parts.append("</table></div>")
    parts.append("</section>")

    # Search
    parts.append("<section>")
    parts.append('<h2 class="section-title">Search</h2>')
    parts.append(
        '<input id="search" type="search" placeholder="Search entries by name, description, or body&hellip;" '
        'autocomplete="off" spellcheck="false">'
    )
    parts.append('<p id="search-count"></p>')
    parts.append("</section>")

    # Entries
    parts.append("<section>")
    parts.append('<h2 class="section-title">Entries</h2>')
    for e in entries:
        badge_class = "badge-shared" if e["shared"] else "badge-private"
        badge_label = "Shareable" if e["shared"] else "Private — stays on this machine"
        rendered_body = render_body(e["body"], slug_lookup)
        search_blob = " ".join([
            e["name"], e["description"], e["type"], e["source"], e["kind"], e["body"],
        ]).lower()
        search_attr = html.escape(search_blob, quote=True)
        captured_label = e["captured"] or "—"
        parts.append(f'<details class="entry" id="entry-{e["slug"]}" data-search="{search_attr}">')
        parts.append("<summary>")
        parts.append(f'<h3 class="entry-name">{html.escape(e["name"])}</h3>')
        parts.append(f'<span class="badge {badge_class}">{badge_label}</span>')
        parts.append("</summary>")
        parts.append(
            f'<p class="entry-meta">{html.escape(e["type"])} &middot; '
            f'{html.escape(e["source"])} &middot; captured {html.escape(captured_label)}</p>'
        )
        if e["description"]:
            parts.append(f'<p class="entry-desc">{html.escape(e["description"])}</p>')
        parts.append(f'<div class="entry-body">{rendered_body}</div>')
        parts.append("</details>")
    parts.append("</section>")

    parts.append(
        '<footer class="report-footer">Loreport report &middot; generated locally, '
        "no external requests.</footer>"
    )

    parts.append("</div>")  # .wrap
    parts.append(f"<script>{JS}</script>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


# --- CLI -----------------------------------------------------------------


def read_version(script_dir):
    version_path = os.path.join(os.path.dirname(script_dir), "VERSION")
    try:
        with open(version_path, "r", encoding="utf-8") as f:
            v = f.read().strip()
            return v or "unknown"
    except OSError:
        return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a self-contained HTML report of a Loreport brain.")
    ap.add_argument("--brain-dir", required=True, help="Path to the brain repo (git working copy).")
    ap.add_argument("--out", default=None, help="Output HTML path (default: <brain-dir>/hub/published/report.html).")
    args = ap.parse_args(argv)

    brain_dir = os.path.abspath(args.brain_dir)
    if not os.path.isdir(brain_dir):
        print(f"error: --brain-dir {brain_dir!r} is not a directory", file=sys.stderr)
        return 1

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.abspath(args.out) if args.out else os.path.join(brain_dir, "hub", "published", "report.html")

    _full_sha, short_sha = resolve_main(brain_dir)
    version = read_version(script_dir)
    providers = load_providers(brain_dir, script_dir)
    activity = load_provider_activity(brain_dir, providers.keys())
    entries = load_entries(brain_dir)
    # Stamp the report with the BRAIN's last-commit time, never the wall clock.
    # This file is tracked in the user's repo, so a wall-clock stamp would rewrite
    # ~800KB on every rebuild and churn the history daily even when nothing about
    # their memories changed. Deriving it from `main` makes the output a pure
    # function of brain content: same brain in, byte-identical file out, so it is
    # only committed when something actually changed. (Same rule INDEX.md follows.)
    now, content_sha = brain_committed_at(brain_dir)

    doc = build_html(version, content_sha, providers, activity, entries, now)

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)

    print(f"wrote {out_path} ({len(doc)} bytes, {len(entries)} entries, main@{short_sha})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
