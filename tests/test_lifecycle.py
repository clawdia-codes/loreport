#!/usr/bin/env python3
"""Tests for the taxonomy/lifecycle layer — `lifespan`, `expires`, `domain`,
INDEX-ARCHIVE.md, and the archive-aware published packet.

The gate list in docs/taxonomy-lifecycle-design.md Phase 2 is used here verbatim
as the test list. The one that matters most is
`test_archived_shared_item_still_reaches_the_packet`: cloud providers get the
packet, not the repo, and cannot lazy-fetch the cold shelf. If archiving dropped
an item's line from INDEX.md and the packet carried INDEX.md alone, every
archived SHARED item would vanish from every cloud assistant's view of the brain
with no error raised anywhere — the same shape as the vacuous privacy check
fixed in 1.8.3, where a safety property held only because nothing was there to
violate it.

Everything here runs against the real code paths (build_index_bytes /
build_archive_index_bytes / build_packet_text over a real git repo), never
against the helpers in isolation, because the failure mode being guarded is a
missing call, not a wrong comparison.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hub"))

from brain_merge import (  # noqa: E402
    build_archive_index_bytes,
    build_index_bytes,
    is_expired,
    parse_expires,
)
from inbox_ingest import validate_schema  # noqa: E402
import snapshot_publish  # noqa: E402


TODAY = date(2026, 8, 7)
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


def _item(name, desc="hook", typ="project", visibility="shared", extra=""):
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        f"type: {typ}\n"
        f"visibility: {visibility}\n"
        f"{extra}"
        "---\n\n"
        f"Body of {name}.\n"
    )


def _block(body, path=None, action="new"):
    name = body.split("name: ", 1)[1].split("\n", 1)[0]
    return {
        "file": path or f"memories/{name}.md",
        "action": action,
        "body": body,
        "index_line": f"INDEX: - [[{name}]] — hook",
        "raw": body,
    }


class ExpiresParsingTests(unittest.TestCase):
    def test_well_formed_date_parses(self):
        self.assertEqual(parse_expires("2026-08-07"), date(2026, 8, 7))

    def test_garbled_date_is_not_expired_rather_than_crashing(self):
        # A broken date must never make an item vanish from the hot index.
        for bad in ("soon", "2026-13-01", "07-08-2026", "", None):
            self.assertIsNone(parse_expires(bad))
            self.assertFalse(is_expired({"expires": bad}, TODAY))

    def test_past_expires_is_expired_and_future_is_not(self):
        self.assertTrue(is_expired({"expires": YESTERDAY}, TODAY))
        self.assertFalse(is_expired({"expires": TOMORROW}, TODAY))

    def test_expiring_exactly_today_is_not_yet_expired(self):
        self.assertFalse(is_expired({"expires": TODAY.isoformat()}, TODAY))

    def test_item_without_expires_is_never_expired(self):
        # This is what makes `permanent`/`active` untouchable by construction
        # rather than by a separate rule that could drift out of sync.
        for fm in ({}, {"lifespan": "permanent"}, {"lifespan": "active"}):
            self.assertFalse(is_expired(fm, TODAY))


class SchemaValidationTests(unittest.TestCase):
    def test_valid_lifespan_and_expires_accepted(self):
        body = _item("trip-oslo", extra=f"lifespan: temporary\nexpires: {TOMORROW}\n")
        self.assertIsNone(validate_schema(_block(body)))

    def test_all_three_fields_are_optional(self):
        self.assertIsNone(validate_schema(_block(_item("plain-note"))))

    def test_unknown_lifespan_rejected(self):
        body = _item("bad-life", extra="lifespan: forever\n")
        self.assertIn("lifespan 'forever'", validate_schema(_block(body)))

    def test_malformed_expires_rejected_at_capture(self):
        for bad in ("soon", "2026-13-01", "07-08-2026"):
            body = _item("bad-exp", extra=f"lifespan: temporary\nexpires: {bad}\n")
            self.assertIn("expires", validate_schema(_block(body)) or "")

    def test_permanent_item_may_not_carry_an_expiry(self):
        body = _item("contradiction", extra=f"lifespan: permanent\nexpires: {TOMORROW}\n")
        self.assertIn("cannot carry an expires date", validate_schema(_block(body)))

    def test_expires_without_lifespan_is_accepted(self):
        # Capture models routinely emit the expiry and omit the lifespan;
        # quarantining an otherwise-valid memory over a missing default would
        # lose real content.
        body = _item("dated-only", extra=f"expires: {TOMORROW}\n")
        self.assertIsNone(validate_schema(_block(body)))

    def test_domain_enum_enforced(self):
        for good in ("work", "personal", "both"):
            body = _item(f"dom-{good}", extra=f"domain: {good}\n")
            self.assertIsNone(validate_schema(_block(body)))
        body = _item("dom-bad", extra="domain: office\n")
        self.assertIn("domain 'office'", validate_schema(_block(body)))

    def test_domain_does_not_affect_visibility(self):
        # The two axes are independent. A work item may be local and a personal
        # item may be shared; nothing may infer one from the other.
        work_local = _item("work-local", visibility="local", extra="domain: work\n")
        personal_shared = _item("personal-shared", visibility="shared", extra="domain: personal\n")
        self.assertIsNone(validate_schema(_block(work_local)))
        self.assertIsNone(validate_schema(_block(personal_shared)))


class IndexSplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loreport-lifecycle-")
        self.addCleanup(_rmtree, self.tmp)
        os.makedirs(os.path.join(self.tmp, "memories"))
        self._write("permanent-note", _item("permanent-note"))
        self._write("active-note", _item("active-note", extra="lifespan: active\n"))
        self._write("expired-note", _item(
            "expired-note", extra=f"lifespan: temporary\nexpires: {YESTERDAY}\n"))
        self._write("future-note", _item(
            "future-note", extra=f"lifespan: temporary\nexpires: {TOMORROW}\n"))

    def _write(self, name, body):
        with open(os.path.join(self.tmp, "memories", f"{name}.md"), "w", encoding="utf-8") as fh:
            fh.write(body)

    def test_expired_item_leaves_the_hot_index(self):
        index = build_index_bytes(self.tmp, today=TODAY)[0].decode()
        self.assertNotIn("[[expired-note]]", index)
        self.assertIn("[[future-note]]", index)

    def test_permanent_and_active_items_are_untouched(self):
        index = build_index_bytes(self.tmp, today=TODAY)[0].decode()
        self.assertIn("[[permanent-note]]", index)
        self.assertIn("[[active-note]]", index)

    def test_hot_index_actually_shrinks(self):
        _, m, _, _ = build_index_bytes(self.tmp, today=TODAY)
        self.assertEqual(m, 3)  # 4 items on disk, 1 archived

    def test_archive_index_holds_exactly_the_expired_item(self):
        archive, n = build_archive_index_bytes(self.tmp, today=TODAY)
        self.assertEqual(n, 1)
        self.assertIn("[[expired-note]]", archive.decode())
        self.assertNotIn("[[permanent-note]]", archive.decode())

    def test_archived_item_file_stays_on_disk(self):
        build_index_bytes(self.tmp, today=TODAY)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "memories", "expired-note.md")))

    def test_hot_and_cold_partition_every_item_exactly_once(self):
        # No item may be dropped by the split, and none may appear in both.
        index = build_index_bytes(self.tmp, today=TODAY)[0].decode()
        archive = build_archive_index_bytes(self.tmp, today=TODAY)[0].decode()
        for name in ("permanent-note", "active-note", "expired-note", "future-note"):
            in_hot = f"[[{name}]]" in index
            in_cold = f"[[{name}]]" in archive
            self.assertTrue(in_hot or in_cold, f"{name} fell out of both indexes")
            self.assertFalse(in_hot and in_cold, f"{name} is in both indexes")

    def test_rebuild_is_deterministic(self):
        first = build_index_bytes(self.tmp, today=TODAY)[0]
        second = build_index_bytes(self.tmp, today=TODAY)[0]
        self.assertEqual(first, second)
        self.assertEqual(
            build_archive_index_bytes(self.tmp, today=TODAY)[0],
            build_archive_index_bytes(self.tmp, today=TODAY)[0],
        )


class PacketArchiveSeamTests(unittest.TestCase):
    """The load-bearing case: archiving must never revoke a cloud provider's
    access to a SHARED item, and must never grant access to a LOCAL one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loreport-packet-")
        self.addCleanup(_rmtree, self.tmp)
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", "-C", self.tmp] + list(a), capture_output=True, text=True, check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.tmp], check=True)
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "test")

        os.makedirs(os.path.join(self.tmp, "memories"))
        os.makedirs(os.path.join(self.tmp, "prompts"))
        self._write("prompts/bootstrap.md", "# bootstrap\n")
        self._write("PROFILE.md", "# profile\n")
        self._write("memories/archived-shared.md", _item(
            "archived-shared", visibility="shared",
            extra=f"lifespan: temporary\nexpires: {YESTERDAY}\n"))
        self._write("memories/archived-local.md", _item(
            "archived-local", visibility="local",
            extra=f"lifespan: temporary\nexpires: {YESTERDAY}\n"))
        self._write("memories/hot-shared.md", _item("hot-shared", visibility="shared"))

        index, _, _, _ = build_index_bytes(self.tmp, today=TODAY)
        archive, _ = build_archive_index_bytes(self.tmp, today=TODAY)
        self._write("INDEX.md", index.decode())
        self._write("INDEX-ARCHIVE.md", archive.decode())
        run("add", "-A")
        run("commit", "-qm", "seed")

    def _write(self, rel, text):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_archived_shared_item_still_reaches_the_packet(self):
        packet = snapshot_publish.build_packet_text(self.tmp)
        self.assertIn("[[archived-shared]]", packet)

    def test_archived_local_item_still_does_not_reach_the_packet(self):
        packet = snapshot_publish.build_packet_text(self.tmp)
        self.assertNotIn("[[archived-local]]", packet)

    def test_hot_shared_item_unaffected(self):
        packet = snapshot_publish.build_packet_text(self.tmp)
        self.assertIn("[[hot-shared]]", packet)

    def test_packet_is_not_vacuously_passing(self):
        # The 1.8.3 lesson: an absence-assertion proves nothing unless the
        # collection is known non-empty. Assert the exact expected shared count.
        packet = snapshot_publish.build_packet_text(self.tmp)
        self.assertEqual(packet.count("[["), 2, "expected exactly the 2 shared items")

    def test_brain_with_no_archive_file_still_publishes(self):
        os.remove(os.path.join(self.tmp, "INDEX-ARCHIVE.md"))
        subprocess.run(["git", "-C", self.tmp, "commit", "-qam", "drop archive"], check=True)
        packet = snapshot_publish.build_packet_text(self.tmp)
        self.assertIn("[[hot-shared]]", packet)
        self.assertNotIn("[[archived-shared]]", packet)


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
