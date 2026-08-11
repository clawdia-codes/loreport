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

# Sibling import, same pattern and same reason as brain_merge.py's `synth_detect`
# import: this module is imported by tooling that does not put hub/ on the path,
# so make the directory importable rather than relying on being run as a script.
#
# The single-file/stdlib-only doctrine is relaxed here deliberately and narrowly:
# hub/attention.py owns the queue schema AND its rendering because "two states,
# ONE rendering path" is a ruling — duplicating the renderer into the projector is
# exactly how one word ends up meaning two conditions. It holds no security
# primitive (no SECRET_PATTERNS, no visibility parser), so it copies none of the
# duplicated-and-checked ones; the visibility decisions below stay here.
sys.path.insert(0, HERE)

import attention  # noqa: E402

BEGIN_MARKER = "<!-- loreport:begin -->"
END_MARKER = "<!-- loreport:end -->"

INDEX_ITEM_RE = re.compile(r"\[\[([^\]]+)\]\]")
TYPE_FROM_LINE_RE = re.compile(
    r"\(\s*(user|feedback|project|reference|knowledge|person|decision|skill)\s*\)\s*$"
)

# PROFILE.md carries `<!-- human:start/end -->` markers (design-wiki-parity §1) that
# the merge guard enforces on. They are bookkeeping for the store, not content for
# the provider, so strip the markers -- never the text between them -- on the way out.
HUMAN_MARKER_LINE_RE = re.compile(r"^[ \t]*<!--\s*human:(?:start|end)\s*-->[ \t]*\n?", re.M)

# The projected block is injected globally, which means it also reaches subagents and
# automated workers spawned inside a session. Those run narrow, delegated tasks: a capture
# from one is un-reviewed by the human, and a reviewer that has absorbed the owner's stated
# preferences is no longer an independent check. Ruling 2026-08-03: scope it by instruction
# now; structural scoping is S3.
SUBAGENT_GUARD = """\
If you are a subagent or automated worker executing a narrow task: use the profile for \
context only; do NOT save memories or act on preferences — capture belongs to main sessions."""

DESKTOP_CAPTURE_POINTER = """\
## Loreport capture
Save durable facts via `loreport_save_memory` (MCP) or emit `<MEMORY>` blocks per `prompts/bootstrap.md`.
Do not edit the Loreport block — nightly sync overwrites it."""

TRUNCATION_ORDER = {
    "reference": 0,
    "project": 1,
    "feedback": 2,
    "user": 2,
    "person": 2,      # entities the owner deals with — as load-bearing as `user`
    "decision": 2,    # rulings; same weight as the `feedback` they were split from
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
    """Return (relpath, abspath) for the first candidate that exists, else
    (None, None). The relpath comes back too because the caller has to know
    whether it resolved a SKILL — skills carry no `visibility:` at all."""
    for relpath in _item_relpaths(name):
        abspath = os.path.join(brain_dir, relpath)
        if os.path.isfile(abspath):
            return relpath, abspath
    return None, None


def _is_shared_visibility_file(path):
    """True ONLY when the item file's frontmatter carries an explicit
    `visibility: shared`.

    This projector writes hub/surface-*.md — files whose entire purpose is to
    be pasted into a cloud assistant. It therefore has to ask the same
    question hub/snapshot_publish.py asks and get the same answer.

    It used to ask the INVERSE question ("does an exact
    `^visibility:\\s*local\\s*$` line appear anywhere?") and include the item
    whenever the answer was no. That is the same fail-OPEN default reached by
    a different route, and it disagreed with the fail-closed
    `_visibility_from_text` on every input the two could differ on: an
    UNMARKED item, `visibility: "local"` (quoted), `visibility: local  # temp`
    (trailing comment), and a file with no frontmatter block at all.
    snapshot_publish withheld all of those from the packet while this function
    put them in the surface — and brain-template/doctor.sh, the only thing
    that audits the surfaces for leaks, used the producer's rule and so
    reported green.

    Frontmatter only: a `visibility: shared` line in prose below the closing
    `---` grants nothing."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    seen = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if not sep or key.strip().lower() != "visibility":
            continue
        seen = value.split("#", 1)[0].strip().strip('"').strip("'").strip().lower()
    return seen == "shared"


def _has_explicit_visibility_file(path):
    """True when the item file's frontmatter carries a `visibility:` key at
    all, whatever its value.

    The disk-reading sibling of hub/brain_merge.py's `_has_explicit_visibility`
    (this projector reads the working tree, not `main`; see
    `_item_file_on_disk`). Same job: `_is_shared_visibility_file` collapses
    "absent" and "explicitly local" into one answer, and the skills carve-out
    below RELAXES a control on the strength of that answer, so it has to tell
    the two apart or it buys the relaxation for a key a human actually wrote.

    Frontmatter only, and unreadable means "no explicit key" — the caller then
    falls through to `_is_shared_visibility_file`, which is also False on an
    unreadable file, so the item is withheld either way."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    if text.startswith("﻿"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, _value = line.partition(":")
        if sep and key.strip().lower() == "visibility":
            return True
    return False


def _name_projectable(brain_dir, name, include_local):
    """True when this item's NAME may appear on this target's surface.

    Same three questions `filter_index_make_surface` asks — one file resolution,
    the skills carve-out, then explicit `visibility: shared` — with one
    deliberate difference: an unresolvable name is withheld here, not kept.
    The filter above keeps an unidentifiable INDEX line because a producer must
    not silently drop what it cannot identify; the attention block is the
    opposite job. It MINTS text about items, so an unidentifiable name is one
    whose visibility nothing has established, and this repo's fail-closed rule
    reads that as `local`.
    """
    if include_local:
        return True
    relpath, item_path = _item_file_on_disk(brain_dir, name)
    if item_path is None:
        return False
    if relpath.startswith("skills/") and not _has_explicit_visibility_file(item_path):
        return True
    return _is_shared_visibility_file(item_path)


def filter_attention_entries(brain_dir, entries, include_local):
    """Withhold entries this target may not carry, and return the names it may
    mark.

    A contested entry is withheld WHOLE when any member fails: the entry's
    content is the PAIRING, so naming the shared half of a shared/local pair
    still discloses that a local item exists and what it is about. Fail closed
    on the set, not per member.

    Without this the cloud surfaces (hub/surface-chatgpt.md,
    hub/surface-claude-ai.md) would carry `[[local-item]]`, which
    hub/brain_audit.py reports as a LEAK — the highest severity there is, and
    correctly so.
    """
    kept = []
    marks = set()
    for entry in entries:
        names = list(entry.get("names") or [])
        if any(not _name_projectable(brain_dir, n, include_local) for n in names):
            continue
        kept.append(entry)
        for name in attention.annotated_names(entry):
            if _name_projectable(brain_dir, name, include_local):
                marks.add(name)
    return kept, marks


def filter_index_make_surface(brain_dir, index_text, include_local):
    """Keep only INDEX lines whose item is explicitly `visibility: shared`,
    unless include_local.

    Two carve-outs, both matching hub/snapshot_publish.py's filter_index_text:
      - a SKILL is a package, not an item, and carries no `visibility:`
        (docs/format-spec.md §1) — kept UNLESS it carries an explicit
        `visibility:` anyway, in which case the human's marking wins. The
        carve-out supplies a default for an absent key; it may not override a
        present one, or `visibility: local` on a SKILL.md becomes a privacy
        control that reports success and still pastes the skill into a cloud
        assistant;
      - an INDEX line whose [[name]] resolves to no file on disk is kept, so
        the filter never silently drops a line it cannot positively identify
        as an item at all."""
    if include_local:
        return index_text, 0

    out_lines = []
    dropped = 0
    for line in index_text.splitlines(keepends=True):
        m = INDEX_ITEM_RE.search(line)
        if not m:
            out_lines.append(line)
            continue
        relpath, item_path = _item_file_on_disk(brain_dir, m.group(1))
        if item_path is None:
            out_lines.append(line)
            continue
        skill_default = (relpath.startswith("skills/")
                         and not _has_explicit_visibility_file(item_path))
        if skill_default or _is_shared_visibility_file(item_path):
            out_lines.append(line)
        else:
            dropped += 1
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
        f"<!-- GENERATED by Loreport "
        f"(main @ {short_sha}) on {today}. DO NOT EDIT — edits are overwritten "
        f"nightly. Save memories via Loreport. -->\n"
    )


def build_attention(brain_dir, index_text, include_local):
    """Return (block_text, annotated_index_text).

    Never raises. This runs inside the projection of five surfaces including the
    primary injected one; an attention queue that cannot be read must not cost
    the user their whole memory. But it is not swallowed either — an unreadable
    queue renders as a visible line in the block, because a silently-empty
    collection is a vacuously-true check, and this repo has shipped that twice.
    """
    try:
        queue = attention.load(brain_dir)
    except attention.QueueUnreadable as exc:
        return (
            "## Loreport — needs your input (unknown)\n"
            f"- ⚠ the attention queue could not be read ({exc}); parked and "
            "contested items are NOT listed below.\n"
            f"  fix: `{attention.RESOLVE_CMD} list`\n"
        ), index_text
    except Exception as exc:  # noqa: BLE001 - see docstring
        return (
            "## Loreport — needs your input (unknown)\n"
            f"- ⚠ the attention queue could not be read ({exc!r}).\n"
        ), index_text

    entries = attention.open_entries(queue)
    if not entries:
        return "", index_text
    entries, marks = filter_attention_entries(brain_dir, entries, include_local)
    if not entries:
        return "", index_text
    block = attention.render_block(
        entries, attention.needs_ask(queue), include_paths=include_local,
    )
    return block, attention.annotate_index_text(index_text, entries, resolvable=marks)


def build_surface_body(brain_dir, target, short_sha):
    include_local = target.get("scope", "shared") == "all"
    profile = HUMAN_MARKER_LINE_RE.sub("", read_brain_text(brain_dir, "PROFILE.md"))
    if not profile.endswith("\n"):
        profile += "\n"

    index_raw = read_brain_text(brain_dir, "INDEX.md")
    index_filtered, vis_dropped = filter_index_make_surface(
        brain_dir, index_raw, include_local,
    )

    attention_block, index_filtered = build_attention(
        brain_dir, index_filtered, include_local,
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

    # The attention block joins the FIXED prefix — a question that gets truncated
    # is a question that never gets asked. It is bounded (attention.MAX_RENDERED)
    # and its bytes are counted against the budget below, so it can push real
    # INDEX lines into `dropped_budget`, which scripts/loreport-health already
    # reports, instead of silently overflowing the surface.
    fixed = header + SUBAGENT_GUARD + "\n\n" + intro + profile + "\n" + attention_block
    if attention_block:
        fixed += "\n"
    budget = int(target["budget_chars"])
    budget_for_index = max(0, budget - len(fixed))
    index_out, budget_dropped = _truncate_index_lines(index_filtered, budget_for_index)
    body = fixed + index_out
    if not body.endswith("\n"):
        body += "\n"

    # PROFILE (and the header/protocol around it) is never truncated: if the fixed
    # part alone blows the budget we go over rather than ship a half-sentence
    # identity. Only whole INDEX lines are ever dropped, above.
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
        "budget_chars": int(target["budget_chars"]),
        "over_budget": len(generated) > int(target["budget_chars"]),
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
        os.getcwd(),
    )


def main():
    parser = argparse.ArgumentParser(
        description="Project Loreport PROFILE+INDEX into per-provider surfaces."
    )
    parser.add_argument("--brain-dir", default=None,
                        help="Brain repo root (default: $LOREPORT_BRAIN_DIR, else the current directory)")
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
