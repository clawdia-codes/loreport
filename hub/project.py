#!/usr/bin/env python3
"""
hub/project.py — Loreport projection into provider surfaces (design-04 §4).

Single-file, Python-3-stdlib-only. After nightly merge/publish, projects PROFILE +
filtered INDEX into per-host targets declared in the brain's hub/projection-targets.json.

Block mode replaces content between <!-- loreport:begin --> / <!-- loreport:end -->
markers (creates the file if absent). Cloud paste artifacts in the brain repo use the
same markers. Writes hub/projection-manifest.json for the §8 health check.

CLI:
    python3 hub/project.py --brain-dir PATH [--dry-run]
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

BEGIN_MARKER = "<!-- loreport:begin -->"
END_MARKER = "<!-- loreport:end -->"

INDEX_ITEM_RE = re.compile(r"\[\[([^\]]+)\]\]")
TYPE_FROM_LINE_RE = re.compile(r"\(\s*(user|feedback|project|reference|knowledge|skill)\s*\)\s*$")

DESKTOP_CAPTURE_POINTER = """\
## Loreport capture
Save durable facts via `loreport_save_memory` (MCP) or emit `<MEMORY>` blocks per `prompts/bootstrap.md`.
Do not edit the Loreport block — nightly sync overwrites it."""

TRUNCATION_ORDER = {
    "reference": 0,
    "project": 1,
    "feedback": 2,
    "user": 2,
    "knowledge": 3,
    "skill": 3,
}


# --- git / paths -------------------------------------------------------------


def _run_git(brain_dir, *args, timeout=30):
    try:
        return subprocess.run(
            ["git", "-C", brain_dir] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)} timed out")


def get_main_sha(brain_dir):
    r = _run_git(brain_dir, "rev-parse", "main")
    if r.returncode != 0:
        raise RuntimeError(f"cannot resolve main in {brain_dir!r}")
    full = r.stdout.strip()
    r2 = _run_git(brain_dir, "rev-parse", "--short", "main")
    short = r2.stdout.strip() if r2.returncode == 0 else full[:7]
    return full, short


def resolve_target_path(brain_dir, path):
    if path.startswith("~/"):
        return os.path.expanduser(path)
    return os.path.join(brain_dir, path)


def read_brain_text(brain_dir, relpath):
    abspath = os.path.join(brain_dir, relpath)
    with open(abspath, "r", encoding="utf-8") as fh:
        return fh.read()


# --- INDEX filtering (make-surface.sh semantics, not snapshot_publish) -------


def _item_relpaths(name):
    return (
        f"memories/{name}.md",
        f"knowledge/{name}.md",
        f"skills/{name}/SKILL.md",
    )


def _item_file_on_disk(brain_dir, name):
    for relpath in _item_relpaths(name):
        abspath = os.path.join(brain_dir, relpath)
        if os.path.isfile(abspath):
            return abspath
    return None


def _is_local_visibility_file(path):
    """True when the item file carries an explicit `visibility: local` line."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if re.match(r"^visibility:\s*local\s*$", line, re.IGNORECASE):
                    return True
    except OSError:
        pass
    return False


def filter_index_make_surface(brain_dir, index_text, include_local):
    """Drop INDEX lines for `visibility: local` items unless include_local."""
    if include_local:
        return index_text, 0

    out_lines = []
    dropped = 0
    for line in index_text.splitlines(keepends=True):
        m = INDEX_ITEM_RE.search(line)
        if not m:
            out_lines.append(line)
            continue
        item_path = _item_file_on_disk(brain_dir, m.group(1))
        if item_path is None:
            out_lines.append(line)
            continue
        if _is_local_visibility_file(item_path):
            dropped += 1
        else:
            out_lines.append(line)
    return "".join(out_lines), dropped


# --- surface assembly --------------------------------------------------------


def _line_type(line):
    m = TYPE_FROM_LINE_RE.search(line.rstrip())
    if m:
        return m.group(1)
    return "knowledge"


def _truncate_index_lines(index_text, budget_for_index):
    """Drop whole INDEX lines, lowest truncation priority first."""
    lines = index_text.splitlines(keepends=True)
    item_idxs = [i for i, ln in enumerate(lines) if INDEX_ITEM_RE.search(ln)]
    if not item_idxs:
        return index_text, 0

    current = sum(len(lines[i]) for i in item_idxs)
    if current <= budget_for_index:
        return index_text, 0

    ranked = sorted(
        item_idxs,
        key=lambda i: (TRUNCATION_ORDER.get(_line_type(lines[i]), 3), i),
    )
    drop_set = set()
    dropped = 0
    for idx in ranked:
        if current <= budget_for_index:
            break
        drop_set.add(idx)
        current -= len(lines[idx])
        dropped += 1

    kept = [ln for i, ln in enumerate(lines) if i not in drop_set]
    return "".join(kept), dropped


def _load_protocol(brain_dir, host):
    bootstrap_path = os.path.join(brain_dir, "prompts", "bootstrap.md")
    with open(bootstrap_path, "r", encoding="utf-8") as fh:
        protocol = fh.read()
    if not protocol.endswith("\n"):
        protocol += "\n"
    if host:
        protocol = protocol.replace(
            "set it here: `____`",
            f"set it here: `{host}`",
        )
    return protocol


def build_header(short_sha):
    today = date.today().isoformat()
    return (
        f"<!-- GENERATED from ~/projects/loreport-oyvind-theie "
        f"(main @ {short_sha}) on {today}. DO NOT EDIT — edits are overwritten "
        f"nightly. Save memories via Loreport. -->\n"
    )


def build_surface_body(brain_dir, target, short_sha):
    include_local = target.get("scope", "shared") == "all"
    profile = read_brain_text(brain_dir, "PROFILE.md")
    if not profile.endswith("\n"):
        profile += "\n"

    index_raw = read_brain_text(brain_dir, "INDEX.md")
    index_filtered, vis_dropped = filter_index_make_surface(
        brain_dir, index_raw, include_local,
    )

    header = build_header(short_sha)

    protocol = target.get("protocol")
    if protocol is None:
        protocol = "full" if target.get("provider") in ("chatgpt", "claude-ai") else "pointer"
    if protocol == "full":
        intro = _load_protocol(brain_dir, target.get("host"))
        if not intro.endswith("\n"):
            intro += "\n"
        intro += "\n"
    else:
        intro = DESKTOP_CAPTURE_POINTER + "\n\n"

    fixed = header + intro + profile + "\n"
    budget = int(target["budget_chars"])
    budget_for_index = max(0, budget - len(fixed))
    index_out, budget_dropped = _truncate_index_lines(index_filtered, budget_for_index)
    body = fixed + index_out
    if not body.endswith("\n"):
        body += "\n"

    if len(body) > budget:
        body = body[:budget]
        if not body.endswith("\n"):
            body += "\n"

    return body, vis_dropped, budget_dropped


# --- block writer ------------------------------------------------------------


def replace_block(existing, generated):
    """Insert or replace the loreport marker block inside existing file text."""
    if BEGIN_MARKER in existing and END_MARKER in existing:
        pre, rest = existing.split(BEGIN_MARKER, 1)
        _, post = rest.split(END_MARKER, 1)
        middle = f"{BEGIN_MARKER}\n{generated}\n{END_MARKER}"
        out = pre + middle + post
    else:
        prefix = existing
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        out = prefix + f"{BEGIN_MARKER}\n{generated}\n{END_MARKER}\n"
    if not out.endswith("\n"):
        out += "\n"
    return out


def extract_block_region(text):
    if BEGIN_MARKER not in text or END_MARKER not in text:
        return text
    _, rest = text.split(BEGIN_MARKER, 1)
    inner, _ = rest.split(END_MARKER, 1)
    return inner.strip("\n") + "\n"


def region_hash(text, mode):
    payload = text if mode == "full" else extract_block_region(text)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def atomic_write(path, content):
    if os.path.islink(path):
        raise OSError(f"refusing to write symlink target: {path}")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    if os.path.islink(tmp):
        raise OSError(f"refusing to write via symlink tmp: {tmp}")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, path)


def write_target(path, generated, mode, dry_run):
    if os.path.lexists(path) and os.path.islink(path):
        raise OSError(f"target is a symlink: {path}")

    if mode == "full":
        final = generated
    else:
        existing = ""
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                existing = fh.read()
        final = replace_block(existing, generated)

    if dry_run:
        return final

    atomic_write(path, final)
    return final


# --- targets / manifest ------------------------------------------------------


def load_targets(brain_dir):
    cfg_path = os.path.join(brain_dir, "hub", "projection-targets.json")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    return cfg["targets"]


def project_one(brain_dir, target, main_sha, short_sha, dry_run):
    path = resolve_target_path(brain_dir, target["path"])
    mode = target.get("mode", "block")
    generated, vis_dropped, budget_dropped = build_surface_body(
        brain_dir, target, short_sha,
    )
    written = write_target(path, generated, mode, dry_run)
    mtime = os.path.getmtime(path) if not dry_run and os.path.isfile(path) else None
    return {
        "provider": target.get("provider", ""),
        "path": target["path"],
        "mode": mode,
        "sha": main_sha,
        "chars": len(written if mode == "full" else extract_block_region(written)),
        "dropped": vis_dropped + budget_dropped,
        "dropped_visibility": vis_dropped,
        "dropped_budget": budget_dropped,
        "mtime": mtime,
        "region_hash": region_hash(written, mode),
    }


def write_manifest(brain_dir, manifest, dry_run):
    out_path = os.path.join(brain_dir, "hub", "projection-manifest.json")
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if dry_run:
        return
    atomic_write(out_path, text)


def default_brain_dir():
    return os.environ.get(
        "LOREPORT_BRAIN_DIR",
        os.path.expanduser("~/projects/loreport-oyvind-theie"),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Project Loreport PROFILE+INDEX into per-provider surfaces."
    )
    parser.add_argument("--brain-dir", default=None,
                        help="Brain repo root (default: ~/projects/loreport-oyvind-theie)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print per-target summary; write nothing")
    args = parser.parse_args()

    brain_dir = os.path.abspath(args.brain_dir or default_brain_dir())
    main_sha, short_sha = get_main_sha(brain_dir)
    targets = load_targets(brain_dir)

    results = []
    failures = 0
    for target in targets:
        try:
            entry = project_one(brain_dir, target, main_sha, short_sha, args.dry_run)
            results.append(entry)
            print(
                f"{entry['provider']:12} {entry['path']:40} "
                f"mode={entry['mode']} chars={entry['chars']} "
                f"dropped={entry['dropped']} sha={short_sha}"
            )
        except Exception as exc:
            failures += 1
            print(
                f"FAIL {target.get('provider', '?')} {target.get('path', '?')}: {exc}",
                file=sys.stderr,
            )

    manifest = {
        "projected_at": date.today().isoformat(),
        "main_sha": main_sha,
        "main_short_sha": short_sha,
        "targets": results,
    }
    try:
        write_manifest(brain_dir, manifest, args.dry_run)
    except Exception as exc:
        failures += 1
        print(f"FAIL manifest: {exc}", file=sys.stderr)

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
