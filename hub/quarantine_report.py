#!/usr/bin/env python3
"""Render the parked blocks under hub/quarantine/ as an alert a human can act on.

WHY THIS EXISTS
The whole alert used to be `Loreport health FAIL: merge digest needs review`.
The owner could not tell from it what was parked, so they asked an agent; the
agent investigated and reported a healthy pipeline as three days dead, and a
second agent repeated the misreading an hour later. Naming the states (1.14.0)
fixed *which* condition is being reported. This module fixes the CONTENT: every
parked block is named, described, and given a copy-pasteable command per
outcome, so the alert answers "what is it and what do I do" without a second
investigation.

Standalone and stdlib-only, like every other hub/*.py: no cross-imports, so it
stays auditable in one sitting. QUARANTINE_NON_ITEMS is therefore a second copy
of brain_merge's tuple; tests/test_alert_content.py pins the two together so the
duplication cannot drift silently.

READ-ONLY. This module never writes to the brain and never deletes a parked
block -- it prints the commands that would, and leaves the decision to a human.

Exits 0 unconditionally. A traceback rendered into the health banner is worse
than a missing detail block, and an empty detail block is worse than either --
so failures are caught and rendered as one explicit line.
"""

import argparse
import os
import re
import sys

# Everything under hub/quarantine/ that is NOT a parked block. Must stay equal
# to brain_merge.QUARANTINE_NON_ITEMS -- pinned by tests/test_alert_content.py,
# because a disagreement would make the count in the digest and the list in the
# alert describe different sets of files.
QUARANTINE_NON_ITEMS = ("digest.md", ".gitkeep")

# The item's own `description:` is the one-line summary a reader needs; the full
# line can be a paragraph. Truncated, not dropped: a phone-sized alert with no
# summary is what sent two agents off to investigate.
DESCRIPTION_MAX_CHARS = 200

# How many item names the one-line summary (the Telegram payload) may carry
# before it collapses to "+N more". Six parked blocks would otherwise push the
# message past a notification preview, which hides the part that says the
# pipeline is healthy.
NOTIFY_MAX_NAMES = 3

MEMORY_TAG_RE = re.compile(r'<MEMORY\b[^>]*\bfile="([^"]+)"', re.IGNORECASE)
MEMORY_ANY_RE = re.compile(r"<MEMORY\b", re.IGNORECASE)
FRONTMATTER_RE = re.compile(r"^---\s*$(.*?)^---\s*$", re.MULTILINE | re.DOTALL)
DIGEST_ENTRY_RE = re.compile(
    r"^- file:\s*(?P<file>.+?)\s*$\n"
    r"(?:^- reason:\s*(?P<reason>.*?)\s*$\n)?"
    r"(?:^- detail:\s*(?P<detail>.*?)\s*$\n)?",
    re.MULTILINE,
)


def _frontmatter(text):
    """Parse the first `---` block in `text` into a flat dict.

    Works for both quarantine shapes: a capture block carries the frontmatter
    inside its <MEMORY> body, a rejected merge update is the item text itself.
    """
    m = FRONTMATTER_RE.search(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in out:
            out[key] = value
    return out


def _truncate(text, limit=DESCRIPTION_MAX_CHARS):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def digest_reasons(brain_dir):
    """Map quarantine relpath -> (reason, detail) from hub/quarantine/digest.md.

    The digest is the only record of WHY a block was parked -- the parked file
    is a verbatim copy of what the provider sent, and carries no verdict.
    """
    path = os.path.join(brain_dir, "hub", "quarantine", "digest.md")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return {}
    out = {}
    for m in DIGEST_ENTRY_RE.finditer(text):
        rel = m.group("file").strip()
        out[rel] = (m.group("reason") or "", m.group("detail") or "")
    return out


def list_pending(brain_dir):
    """Every parked block under hub/quarantine/, described.

    Sorted by relpath so the rendering (and therefore the alert signature) is
    stable across runs when nothing has changed.
    """
    qdir = os.path.join(brain_dir, "hub", "quarantine")
    reasons = digest_reasons(brain_dir)
    items = []
    if not os.path.isdir(qdir):
        return items
    for root, _dirs, files in os.walk(qdir):
        for fname in files:
            if fname in QUARANTINE_NON_ITEMS:
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, brain_dir)
            provider = os.path.basename(root)
            if os.path.abspath(root) == os.path.abspath(qdir):
                provider = ""
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError as exc:
                items.append({
                    "path": path, "relpath": rel, "provider": provider,
                    "filename": fname, "kind": "unreadable",
                    "name": "", "type": "", "visibility": "",
                    "description": f"could not read the parked file: {exc}",
                    "reason": reasons.get(rel, ("", ""))[0],
                    "detail": reasons.get(rel, ("", ""))[1],
                    "target": "",
                })
                continue
            fm = _frontmatter(text)
            tag = MEMORY_TAG_RE.search(text)
            if MEMORY_ANY_RE.search(text):
                kind = "capture"
            elif fm:
                kind = "merge-update"
            else:
                kind = "unknown"
            target = tag.group(1) if tag else ""
            if not target and kind == "merge-update":
                # quarantine_merge_update names the file <date>-<relpath with
                # os.sep replaced by "__">, so the item path is recoverable.
                stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", fname)
                stem = re.sub(r"\.\d+$", "", stem)
                if "__" in stem:
                    target = stem.replace("__", "/")
            name = fm.get("name") or (
                os.path.splitext(os.path.basename(target))[0] if target else "")
            reason, detail = reasons.get(rel, ("", ""))
            items.append({
                "path": path, "relpath": rel, "provider": provider,
                "filename": fname, "kind": kind,
                "name": name,
                "type": fm.get("type", ""),
                "visibility": fm.get("visibility", ""),
                "description": fm.get("description", ""),
                "reason": reason, "detail": detail,
                "target": target,
            })
    items.sort(key=lambda d: d["relpath"])
    return items


def summary_names(items, limit=NOTIFY_MAX_NAMES):
    """The one-line name list for the phone-sized payload."""
    labels = [(it["name"] or it["filename"]) for it in items]
    if not labels:
        return ""
    shown = labels[:limit]
    text = ", ".join(shown)
    if len(labels) > len(shown):
        text += f" (+{len(labels) - len(shown)} more)"
    return text


def _commands(item, brain_dir, engine_dir):
    """show / accept / discard for one parked block.

    `accept` is deliberately NOT a generic "copy it into place" line. A rejected
    merge update applied by hand bypasses the secret scrub, the visibility
    classification and the provenance revert -- in a brain whose own history
    includes three fail-open visibility leaks in a single day. Only a capture
    block gets an automatic accept, because re-running inbox_ingest.py re-runs
    all five gates; anything else is told, in words, why there is no one-liner.

    `--trust local` is deliberate, not a shortcut. It relaxes only the ownership
    check, which is the tier a human running this by hand on their own machine
    actually has; the parse, schema, secret and imperative gates all still run.
    Under `cloud` the ownership check refuses every visibility:local item, so the
    command printed in the alert would re-quarantine the very blocks most likely
    to be parked -- and a command that fails when you paste it teaches the reader
    to distrust the whole alert.
    """
    path = item["path"]
    out = [("show", f"cat '{path}'")]
    if item["kind"] == "capture" and item["provider"]:
        ingest = os.path.join(engine_dir, "hub", "inbox_ingest.py")
        out.append((
            "accept (re-runs the capture gate)",
            f"python3 '{ingest}' {item['provider']} '{path}' "
            f"--brain-dir '{brain_dir}' --trust local",
        ))
    else:
        target = item["target"] or "the item it was meant to update"
        out.append((
            "accept",
            "not automatic — this is a rejected merge update, not a capture "
            "block, and applying it by hand skips the secret scrub. Compare it "
            f"with {target} and re-file it through a provider branch.",
        ))
    out.append(("discard (deletes the parked copy)", f"rm '{path}'"))
    return out


def render(brain_dir, engine_dir, digest_count=None, indent="    "):
    """The detail block the health banner prints under its review line."""
    items = list_pending(brain_dir)
    qdir = os.path.join(brain_dir, "hub", "quarantine")
    lines = []

    # The digest's count is committed; hub/quarantine/ is gitignored and
    # ephemeral. When they disagree, SAY SO. A "2 blocks pending" header over an
    # empty list is the vacuously-true shape that has shipped here twice, and it
    # reads as "nothing is actually wrong" -- the opposite of the truth.
    if digest_count is not None and digest_count != len(items):
        lines.append(
            f"count mismatch: the merge digest counted {digest_count} parked "
            f"block(s), {len(items)} are on disk now under {qdir} — "
            "hub/quarantine/ is gitignored, so list that directory by hand "
            "before deciding anything."
        )

    if not items:
        lines.append(
            f"no parked files found under {qdir} (it is gitignored, so a block "
            "exists only as its on-disk copy) — list the directory by hand."
        )
        return "\n".join(indent + ln for ln in lines)

    lines.append(f"parked blocks under {qdir} ({len(items)}):")
    for n, item in enumerate(items, 1):
        label = item["name"] or item["filename"]
        lines.append("")
        lines.append(
            f"{n}. {label}  [type: {item['type'] or 'unset'} · "
            f"visibility: {item['visibility'] or 'unset'} · "
            f"from: {item['provider'] or 'unknown provider'}]"
        )
        reason = item["reason"] or "not recorded in hub/quarantine/digest.md"
        detail = item["detail"]
        lines.append(f"   parked because: {reason}"
                     + (f" — {_truncate(detail)}" if detail else ""))
        desc = item["description"]
        lines.append(f"   description: {_truncate(desc) if desc else '(none in the block)'}")
        lines.append(f"   file: {item['relpath']}")
        for verb, cmd in _commands(item, brain_dir, engine_dir):
            lines.append(f"   {verb}: {cmd}")
    return "\n".join(indent + ln if ln else "" for ln in lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--brain-dir", required=True)
    parser.add_argument("--engine-dir", default="")
    parser.add_argument("--digest-count", default="")
    parser.add_argument("--mode", choices=("banner", "names"), default="banner")
    args = parser.parse_args()
    try:
        count = int(args.digest_count) if args.digest_count.strip() else None
    except ValueError:
        count = None
    try:
        if args.mode == "names":
            sys.stdout.write(summary_names(list_pending(args.brain_dir)) + "\n")
        else:
            sys.stdout.write(render(args.brain_dir, args.engine_dir, count) + "\n")
    except Exception as exc:  # noqa: BLE001 - see module docstring
        sys.stdout.write(
            f"    could not enumerate hub/quarantine/: {exc} — "
            "list the directory by hand.\n")
    # Always 0: this is a report emitter, and the caller's exit code means
    # something else entirely.
    return 0


if __name__ == "__main__":
    sys.exit(main())
