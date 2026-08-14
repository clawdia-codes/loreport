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
import shlex
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

# Longest single item name the phone-sized summary may carry. The names are read
# out of a block that FAILED the schema gate, so `name:` is unvalidated caller
# text: one 600-char name would fill the whole notification and, because the
# caller appends its actionable "what to do:" pointer BEFORE clamping, evict
# precisely the actionable part. Cap each name, not just how many there are.
NOTIFY_MAX_NAME_CHARS = 60

# Reasons whose accept MUST NOT be a paste-and-run line, because the gate that
# refused the block is the one `--trust local` switches off. Re-running an
# `ownership-denied` capture at local trust does not re-decide the refusal --
# check_ownership() returns None on its first line — and the commit it produces
# carries `Trust: local`, which brain_merge.collect_provenance_violations()
# skips outright. Two independent defences against a cross-provider takeover,
# waived by one line the alert itself printed. These get words, not a command.
OWNERSHIP_WAIVED_REASONS = ("ownership-denied",)

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
    """The one-line name list for the phone-sized payload.

    Each label is capped as well as the count: see NOTIFY_MAX_NAME_CHARS.
    """
    labels = [_truncate(it["name"] or it["filename"], NOTIFY_MAX_NAME_CHARS)
              for it in items]
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
    block gets an automatic accept, and only when the reason it was parked is
    one that re-running the gate can actually re-decide; anything else is told,
    in words, why there is no one-liner.

    `--trust local` on that accept RELAXES the ownership check and leaves the
    other four gates (parse, schema, secret, imperative) in force. That is the
    right tier for a human at their own machine re-filing their own block, and
    it is why a `visibility: local` item can be accepted at all. It is exactly
    the WRONG tier for OWNERSHIP_WAIVED_REASONS below, where the ownership
    check is the thing that made the decision -- see that constant.

    A verb is never labelled with a gate it does not run: `accept (re-runs the
    capture gate)` means the five gates run with ownership at local trust, and
    for an ownership refusal there is no accept line at all.
    """
    path = item["path"]
    out = [("show", f"cat {shlex.quote(path)}")]
    if item["kind"] == "capture" and item["reason"] in OWNERSHIP_WAIVED_REASONS:
        target = item["target"] or "the item it was meant to update"
        out.append((
            "accept",
            "not automatic — this block was refused BY the ownership check "
            f"(reason: {item['reason']}), and the only way to re-run the "
            "capture with it satisfied is `--trust local`, which switches that "
            "check off rather than re-running it. The resulting commit would "
            "also be stamped `Trust: local`, which makes the merge-side "
            "provenance backstop skip it too — two gates waived by one pasted "
            f"line. Compare the block with {target} and, if the takeover really "
            "is intended, change that item's `visibility:`/`source:` on main "
            "first and let the provider re-capture it normally.",
        ))
    elif item["kind"] == "capture" and item["provider"]:
        ingest = os.path.join(engine_dir, "hub", "inbox_ingest.py")
        out.append((
            "accept (re-runs the capture gate)",
            f"python3 {shlex.quote(ingest)} {shlex.quote(item['provider'])} "
            f"{shlex.quote(path)} "
            f"--brain-dir {shlex.quote(brain_dir)} --trust local",
        ))
    elif item["kind"] == "capture":
        # A capture we cannot re-run: no provider could be determined.
        out.append((
            "accept",
            "not automatic — this is a CAPTURE with no provider recorded "
            f"(reason: {item['reason']}), so there is no command that re-runs "
            "it through the gate. Read it with `show` above and re-emit it from "
            "a session that can.",
        ))
    elif item["kind"] == "merge-update":
        target = item["target"] or "the item it was meant to update"
        out.append((
            "accept",
            "not automatic — this is a rejected merge update, not a capture "
            "block, and applying it by hand skips the secret scrub. Compare it "
            f"with {target} and re-file it through a provider branch.",
        ))
    else:
        # `kind` is inferred from CONTENT: a <MEMORY> tag means capture,
        # frontmatter means merge-update, NEITHER means unknown — which is
        # exactly what an `empty-block` (0-byte) quarantine looks like.
        #
        # This branch used to be the merge-update branch, so every unknown was
        # told "this is a rejected merge update, not a capture block": a
        # subsystem the code had never determined, with a remedy that does not
        # apply, stated as fact. Naming the wrong subsystem had two separate
        # agents report a healthy brain as three days dead this week, so an
        # honest "I cannot tell" is strictly better than a confident guess.
        out.append((
            "accept",
            "not automatic — this file could not be classified: it carries "
            "neither a <MEMORY> block nor frontmatter, so it is neither a "
            f"recognisable capture nor a merge update (reason: {item['reason']}). "
            "A 0-byte file means the emitting session sent nothing, and the "
            "content is recoverable only from that session, not from here. Read "
            "it with `show` above before discarding.",
        ))
    out.append(("discard (deletes the parked copy)", f"rm {shlex.quote(path)}"))
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
