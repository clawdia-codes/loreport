#!/usr/bin/env python3
"""
hub/snapshot_publish.py — pinned-packet build + fail-closed egress scrub
(design.md §D16, modules.md M13d).

Single-file, Python-3-stdlib-only.

Pipeline:
  1. Build the packet — read from `main`: prompts/bootstrap.md + PROFILE.md +
     INDEX.md, concatenated in that order, with a footer comment
     `<!-- loreport packet: main@<short-commit-hash> <date> -->`.
     The packet contains EXACTLY these three files, nothing else — no detail
     file bodies, ever.
  2. Fail-closed egress scrub — the same secret-regex patterns as
     brain_merge.py, run over the assembled packet. Any hit blocks the ENTIRE
     republish (exit nonzero, alert to stdout, alert written to
     hub/quarantine/digest.md). Never writes a partial packet.
  3. Deliver — on scrub pass: write hub/published/packet.md (current pinned
     packet) and hub/published/packet-<date>.md (archive copy).

CLI:
    python3 hub/snapshot_publish.py [--brain-dir PATH] [--dry-run] [--test-scrub]
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date

# Identical secret-regex set to brain_merge.py / inbox_ingest.py (duplicated on
# purpose — every hub/*.py file is single-file and stdlib-only).
SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9-]{20,}",
    r"ghp_[A-Za-z0-9]{36}",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}",
]


def scan_secrets(text):
    for pat in SECRET_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return None


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if not text.endswith("\n"):
        text += "\n"
    return text


def get_commit_hash(brain_dir):
    try:
        r = subprocess.run(["git", "-C", brain_dir, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    except OSError:
        pass
    return "unknown"


def build_packet_text(brain_dir):
    """The §D1 operating surface, byte-for-byte: bootstrap + PROFILE + INDEX. Never
    widened with detail-file bodies (memories/, knowledge/, skills/ are excluded)."""
    bootstrap_path = os.path.join(brain_dir, "prompts", "bootstrap.md")
    profile_path = os.path.join(brain_dir, "PROFILE.md")
    index_path = os.path.join(brain_dir, "INDEX.md")
    for p in (bootstrap_path, profile_path, index_path):
        if not os.path.isfile(p):
            raise FileNotFoundError(p)

    bootstrap = read_text(bootstrap_path)
    profile = read_text(profile_path)
    index = read_text(index_path)

    commit_hash = get_commit_hash(brain_dir)
    today = date.today().isoformat()
    footer = f"<!-- loreport packet: main@{commit_hash} {today} -->\n"

    return bootstrap + profile + index + footer


def write_alert(brain_dir, hit):
    digest_path = os.path.join(brain_dir, "hub", "quarantine", "digest.md")
    os.makedirs(os.path.dirname(digest_path), exist_ok=True)
    is_new = not os.path.isfile(digest_path)
    from datetime import datetime
    ts = datetime.now().isoformat(timespec="seconds")
    masked = hit[:6] + "…" if len(hit) > 6 else hit
    with open(digest_path, "a", encoding="utf-8") as fh:
        if is_new:
            fh.write("# Quarantine digest\n\n"
                     "Every block that failed the inbox_ingest.py scan-before-commit\n"
                     "gate is logged here — nothing is silently dropped.\n\n")
        fh.write(f"## {ts} — PUBLISH BLOCKED (egress scrub)\n")
        fh.write(f"- reason: secret found in packet: {masked}\n")
        fh.write("- action: rotate the secret, then re-run snapshot_publish.py\n\n")


def default_brain_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_test_scrub(brain_dir):
    """Inject a fake secret into the assembled packet and verify the scrub blocks
    it. Never writes hub/published/ — this is an in-memory self-test."""
    try:
        packet = build_packet_text(brain_dir)
    except FileNotFoundError as e:
        print(f"ERROR: cannot build packet, missing file: {e}")
        sys.exit(1)

    poisoned = packet + "\nInjected test credential: sk-FAKE-item5-scrubme-0000\n"
    hit = scan_secrets(poisoned)
    if hit:
        print(f"PUBLISH BLOCKED: secret found in packet — {hit} Rotate the secret first.")
        write_alert(brain_dir, hit)
        sys.exit(1)
    print("FAIL: scrub did not detect the injected test secret")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Build the pinned PROFILE+INDEX publish packet with a fail-closed "
                    "egress secret scrub."
    )
    parser.add_argument("--brain-dir", default=None,
                        help="Brain repo root (default: inferred from this script's location)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build and scrub the packet, print it, write nothing")
    parser.add_argument("--test-scrub", action="store_true",
                        help="Inject a fake secret and verify the scrub blocks the republish")
    args = parser.parse_args()

    brain_dir = args.brain_dir or default_brain_dir()

    if args.test_scrub:
        run_test_scrub(brain_dir)
        return

    try:
        packet = build_packet_text(brain_dir)
    except FileNotFoundError as e:
        print(f"ERROR: cannot build packet, missing file: {e}")
        sys.exit(1)

    hit = scan_secrets(packet)
    if hit:
        print(f"PUBLISH BLOCKED: secret found in packet — {hit} Rotate the secret first.")
        write_alert(brain_dir, hit)
        sys.exit(1)

    if args.dry_run:
        print(packet)
        print("--- dry-run: no files written ---")
        return

    published_dir = os.path.join(brain_dir, "hub", "published")
    os.makedirs(published_dir, exist_ok=True)
    today = date.today().isoformat()
    with open(os.path.join(published_dir, "packet.md"), "w", encoding="utf-8") as fh:
        fh.write(packet)
    with open(os.path.join(published_dir, f"packet-{today}.md"), "w", encoding="utf-8") as fh:
        fh.write(packet)
    print(f"Published packet.md ({len(packet)} bytes) and packet-{today}.md")


if __name__ == "__main__":
    main()
