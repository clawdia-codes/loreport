#!/usr/bin/env python3
"""Tests for the fail-closed `visibility:` default (P2).

`docs/format-spec.md` §1 used to make `visibility:` optional and default it to
`shared` — cloud-published. `hub/snapshot_publish.py`'s filter was never wrong:
it drops items marked `local`. The defect was that nothing asserted an item had
been classified AT ALL, so an unmarked item was not merely unfiltered, it was
positively PUBLISHED. Three batches leaked that way on 2026-08-07.

Two changes close it, and both are tested here because either alone is a
half-measure:

  - the default flips to `local`, in every place it is materialised — the five
    `_visibility_from_text` copies AND the separate, divergent exact-line rule
    in `hub/project.py` / `make-surface.sh`, which was the actual leak path
    into the paste-into-cloud surfaces; and
  - `snapshot_publish` refuses to publish while any item is unclassified, so
    the safe default cannot quietly accumulate a pile of items no provider can
    see. A silent fail-closed default just moves the problem.

Two carve-outs are load-bearing and get regression tests of their own, because
each is a place where "fail closed" would otherwise do damage:

  - SKILLS. A skill is a package, not an item, and carries no `visibility:`
    field at all (format-spec.md §1). Run through the flipped parser bare it
    would come back `local` and vanish from every packet and surface — this is
    not hypothetical, the three skills in the author's live brain are exactly
    such files. The carve-out lives at the RESOLVERS, which know a path is a
    skill; the parser only ever sees text.
  - THE SECRET-SCRUB SPLIT in `brain_merge`. It demotes a secret hit from
    "abort the merge" to "a line in a digest" for `local` items. Routing that
    through the flipped parser would silently extend the demotion to every
    unmarked item — a directional LOOSENING bought by a default meant to
    tighten. It requires an explicit `visibility: local`.

Every test below names the single-line production mutation it reddens.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HUB = os.path.join(os.path.dirname(HERE), "hub")
sys.path.insert(0, HUB)

import brain_merge          # noqa: E402
import inbox_ingest         # noqa: E402
import mcp_server           # noqa: E402
import project              # noqa: E402
import report_build         # noqa: E402
import snapshot_publish     # noqa: E402


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True, check=check)


MARKED = """---
name: {name}
description: {name} hook
type: project
visibility: {vis}
---

Body of {name}.
"""

UNMARKED = """---
name: {name}
description: {name} hook
type: project
---

Body of {name}.
"""

SKILL = """---
name: {name}
description: {name} hook
---

# {name}

Procedure.
"""


class ParserDefaultTests(unittest.TestCase):
    """The default itself, in every copy that materialises it."""

    # The five copies of the parser are deliberately duplicated (no cross-imports
    # between hub/*.py). scripts/check-docs.sh proves they are byte-identical;
    # this proves the shared text says the right thing. report_build's copy is
    # the odd one out — same rule, "private" instead of "local" — so it is
    # compared on its own wording.
    PARSERS = [
        ("brain_merge", brain_merge._visibility_from_text),
        ("inbox_ingest", inbox_ingest._visibility_from_text),
        ("mcp_server", mcp_server._visibility_from_text),
        ("snapshot_publish", snapshot_publish._visibility_from_text),
    ]

    def test_unmarked_item_is_local_in_every_parser(self):
        """MUTATION: in hub/brain_merge.py `_visibility_from_text`, restore the
        old absent-field default by changing the final line to
        `return "shared" if seen in (None, "shared") else "local"`.
        (check-docs.sh then also reports the other three copies as DRIFTED.)"""
        text = UNMARKED.format(name="never-classified")
        for label, fn in self.PARSERS:
            with self.subTest(parser=label):
                self.assertEqual(fn(text), "local")
        self.assertEqual(report_build.visibility_from_text(text), "private")

    def test_explicit_shared_still_shared_in_every_parser(self):
        """The guard against over-correcting. MUTATION: change the same final
        line to `return "local"` — a parser that withholds everything is
        "fail-closed" and useless, and every leak test above would still pass."""
        text = MARKED.format(name="deliberately-shared", vis="shared")
        for label, fn in self.PARSERS:
            with self.subTest(parser=label):
                self.assertEqual(fn(text), "shared")
        self.assertEqual(report_build.visibility_from_text(text), "shared")

    def test_has_explicit_visibility_separates_absent_from_local(self):
        """`_visibility_from_text` collapses absent/malformed/local into `local`
        on purpose; the two callers that RELAX a control on `local` must not.
        MUTATION: in `_has_explicit_visibility`, change the loop body's
        `if sep and key.strip().lower() == "visibility": return True` to
        `return True` — every item then reads as explicitly classified."""
        for fn in (brain_merge._has_explicit_visibility,
                   snapshot_publish._has_explicit_visibility):
            self.assertTrue(fn(MARKED.format(name="a", vis="local")))
            self.assertTrue(fn(MARKED.format(name="a", vis="shared")))
            self.assertFalse(fn(UNMARKED.format(name="a")))
            self.assertFalse(fn("no frontmatter here\n"))


class BrainRepo:
    """A real brain-shaped git repo on `main`. snapshot_publish reads only via
    `git show main:<path>`, so a plain directory fixture would not exercise it."""

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="loreport-vis-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "test")
        self.write("prompts/bootstrap.md", "# bootstrap\n")
        self.write("PROFILE.md", "# profile\n")
        self.item("shared-one", "shared")
        self.item("local-one", "local")
        self.skill("a-skill")
        self.write_index()
        self.commit("seed")

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def item(self, name, vis=None):
        body = (MARKED.format(name=name, vis=vis) if vis
                else UNMARKED.format(name=name))
        self.write(f"memories/{name}.md", body)

    def skill(self, name):
        self.write(f"skills/{name}/SKILL.md", SKILL.format(name=name))

    def write_index(self):
        names = sorted(
            f[:-3] for f in os.listdir(os.path.join(self.repo, "memories"))
        )
        skills = sorted(os.listdir(os.path.join(self.repo, "skills")))
        lines = ["# Index\n", "\n", "## Memories\n"]
        lines += [f"- [[{n}]] — {n} hook  (project)\n" for n in names]
        lines += ["\n", "## Skills\n"]
        lines += [f"- [[{s}]] — {s} hook  (skill)\n" for s in skills]
        self.write("INDEX.md", "".join(lines))

    def commit(self, msg):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", msg)


class PacketFilterTests(BrainRepo, unittest.TestCase):

    def packet(self):
        return snapshot_publish.build_packet_text(self.repo)

    def test_unmarked_item_is_not_published(self):
        """The leak itself. MUTATION: same final-line revert in
        `snapshot_publish._visibility_from_text` as above."""
        self.item("never-classified")
        self.write_index()
        self.commit("add an unclassified item")
        self.assertNotIn("never-classified", self.packet())

    def test_shared_item_is_still_published(self):
        """Regression guard. MUTATION: in
        `snapshot_publish._visibility_from_text`, `return "local"`."""
        self.assertIn("shared-one", self.packet())

    def test_local_item_is_still_withheld(self):
        self.assertNotIn("local-one", self.packet())

    def test_skill_is_still_published_though_it_has_no_visibility_field(self):
        """The carve-out. A skill carries no `visibility:` at all, so the
        flipped parser reads it as `local` — without this, flipping the default
        silently deletes every skill from the packet.
        MUTATION: in `snapshot_publish._item_visibility`, delete the
        `if relpath.startswith("skills/"): return "shared"` line."""
        self.assertIn("a-skill", self.packet())


class PublishGateTests(BrainRepo, unittest.TestCase):

    def run_publish(self):
        env = dict(os.environ, PYTHONPATH=HUB)
        return subprocess.run(
            [sys.executable, os.path.join(HUB, "snapshot_publish.py"),
             "--brain-dir", self.repo],
            capture_output=True, text=True, env=env,
        )

    def packet_path(self):
        return os.path.join(self.repo, "hub", "published", "packet.md")

    def test_publish_succeeds_when_everything_is_classified(self):
        """Regression guard: the gate must not block a healthy brain.
        MUTATION: in `snapshot_publish.main`, change `if unclassified:` to
        `if True:`."""
        r = self.run_publish()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(self.packet_path()))

    def test_unclassified_item_blocks_the_whole_publish(self):
        """MUTATION: in `snapshot_publish.main`, change `if unclassified:` to
        `if False:` — the item is still withheld from the packet, silently,
        which is exactly the pile this gate exists to prevent."""
        self.item("never-classified")
        self.write_index()
        self.commit("add an unclassified item")
        r = self.run_publish()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("never-classified", r.stdout)
        self.assertFalse(os.path.isfile(self.packet_path()),
                         "blocked publish must not write a packet")

    def test_blocked_publish_names_the_item_in_the_quarantine_digest(self):
        """The alert has to survive the process exiting, or nothing but a
        scrollback ever knew. MUTATION: in `snapshot_publish.main`, delete the
        `write_unclassified_alert(brain_dir, unclassified)` call."""
        self.item("never-classified")
        self.write_index()
        self.commit("add an unclassified item")
        self.run_publish()
        digest = os.path.join(self.repo, "hub", "quarantine", "digest.md")
        self.assertTrue(os.path.isfile(digest))
        with open(digest, encoding="utf-8") as fh:
            self.assertIn("never-classified", fh.read())

    def test_gate_ignores_skills(self):
        """A skill has no `visibility:` field to omit, so it must never be
        counted as unclassified — otherwise the gate blocks publishing forever
        on any brain that has a skill, which is every brain.
        MUTATION: in `find_unclassified_items`, change the prefix filter to
        `if not relpath.endswith(".md"): continue` alone."""
        self.assertEqual(snapshot_publish.find_unclassified_items(self.repo), [])


class SurfaceProjectionTests(BrainRepo, unittest.TestCase):
    """hub/project.py writes hub/surface-*.md — files whose whole purpose is to
    be pasted into a cloud assistant. It carried its own, divergent rule
    ("include unless an exact `^visibility:\\s*local\\s*$` line appears"), so it
    published everything the fail-closed parser withheld. Flipping the parser
    alone would have left this path wide open."""

    def filtered(self):
        with open(os.path.join(self.repo, "INDEX.md"), encoding="utf-8") as fh:
            index = fh.read()
        text, _dropped = project.filter_index_make_surface(self.repo, index, False)
        return text

    def test_unmarked_item_is_withheld_from_the_surface(self):
        """MUTATION: in `project.filter_index_make_surface`, replace the keep
        condition with the old rule —
        `if relpath.startswith("skills/") or not re.search(r"(?im)^visibility:\\s*local\\s*$", open(item_path).read()):`"""
        self.item("never-classified")
        self.write_index()
        self.assertNotIn("never-classified", self.filtered())

    def test_quoted_and_commented_local_are_withheld_from_the_surface(self):
        """The divergence that made the two rules disagree on real inputs.
        Same mutation as above: the old exact-line regex matches neither
        `visibility: "local"` nor `visibility: local  # temp`, so both were
        projected into a cloud surface while the packet withheld them."""
        self.write("memories/quoted-local.md",
                   MARKED.format(name="quoted-local", vis='"local"'))
        self.write("memories/commented-local.md",
                   MARKED.format(name="commented-local", vis="local  # temp"))
        self.write_index()
        out = self.filtered()
        self.assertNotIn("quoted-local", out)
        self.assertNotIn("commented-local", out)

    def test_shared_item_and_skill_still_reach_the_surface(self):
        """Regression guard, and the reason it matters here specifically: a
        fail-closed bug in this filter empties the surface instead of leaking
        it, which no leak test would catch.
        MUTATION: in `project._is_shared_visibility_file`, `return False`."""
        out = self.filtered()
        self.assertIn("shared-one", out)
        self.assertIn("a-skill", out)
        self.assertNotIn("local-one", out)


class SecretScrubSplitTests(unittest.TestCase):
    """`scan_brain_for_secrets` may demote a hit to a non-blocking warning for a
    `local` item. The flipped parser calls an unmarked item `local` — so routing
    the demotion through it would quietly stop the merge aborting on secrets in
    unclassified items. The warnings bucket is only defensible because a `local`
    item is KNOWN to be withheld; an unmarked one is not known to be anything."""

    SECRET = "ghp_" + "a" * 36

    def setUp(self):
        self.brain = tempfile.mkdtemp(prefix="loreport-scrub-")
        self.addCleanup(shutil.rmtree, self.brain, True)
        os.makedirs(os.path.join(self.brain, "memories"))

    def write_item(self, name, vis=None):
        body = (MARKED.format(name=name, vis=vis) if vis
                else UNMARKED.format(name=name))
        with open(os.path.join(self.brain, "memories", f"{name}.md"), "w") as fh:
            fh.write(body + f"\ntoken: {self.SECRET}\n")

    def test_secret_in_unmarked_item_still_aborts_the_merge(self):
        """MUTATION: in `brain_merge.scan_brain_for_secrets`, change the
        `explicitly_local = ...` assignment to
        `explicitly_local = _visibility_from_text(text) == "local"` — i.e. drop
        the `_has_explicit_visibility` half. The hit silently moves from
        fail_closed to warnings and the merge stops aborting."""
        self.write_item("never-classified")
        fail_closed, warnings = brain_merge.scan_brain_for_secrets(self.brain)
        self.assertIsNotNone(fail_closed)
        self.assertEqual(fail_closed[0], os.path.join("memories", "never-classified.md"))
        self.assertEqual(warnings, [])

    def test_secret_in_explicitly_local_item_is_still_only_a_warning(self):
        """Regression guard for the same line: the demotion must survive for an
        item a human actually marked local.
        MUTATION: in the same assignment, drop the second half —
        `explicitly_local = _has_explicit_visibility(text)`."""
        self.write_item("marked-local", "local")
        fail_closed, warnings = brain_merge.scan_brain_for_secrets(self.brain)
        self.assertIsNone(fail_closed)
        self.assertEqual([w[0] for w in warnings],
                         [os.path.join("memories", "marked-local.md")])

    def test_secret_in_shared_item_still_aborts_the_merge(self):
        self.write_item("marked-shared", "shared")
        fail_closed, _warnings = brain_merge.scan_brain_for_secrets(self.brain)
        self.assertIsNotNone(fail_closed)


class OwnershipTests(BrainRepo, unittest.TestCase):
    """`inbox_ingest.check_ownership` treats a `visibility: local` item on main
    as update-protected against a cloud provider. The flipped default extends
    that protection to unclassified items — fail-closed, and visible: the block
    is quarantined with a reason rather than dropped."""

    def test_unmarked_item_is_update_protected_from_a_cloud_provider(self):
        """MUTATION: same final-line revert in
        `inbox_ingest._visibility_from_text`.

        The item carries `source: chatgpt` deliberately. `check_ownership`
        denies on EITHER a `local` visibility OR a foreign `source:`, and an
        unmarked test item has no `source:` at all — so without this the test
        would pass under the old fail-open default too, on the source check
        alone, and prove nothing about visibility. (Found by running the
        mutation: the first version of this test stayed green.)"""
        self.write("memories/never-classified.md",
                   "---\nname: never-classified\ndescription: h\ntype: project\n"
                   "source: chatgpt\n---\n\nBody.\n")
        self.write_index()
        self.commit("add an unclassified item")
        reason = inbox_ingest.check_ownership(
            self.repo, "chatgpt", "cloud", "memories/never-classified.md")
        self.assertIsNotNone(reason)
        self.assertIn("visibility", reason)

    def test_shared_item_owned_by_the_provider_is_still_updatable(self):
        """Regression guard: the ownership check must not start refusing
        everything. MUTATION: in `inbox_ingest.check_ownership`, `return
        "denied"` unconditionally."""
        self.write("memories/owned.md",
                   "---\nname: owned\ndescription: h\ntype: project\n"
                   "source: chatgpt\nvisibility: shared\n---\n\nBody.\n")
        self.write_index()
        self.commit("add a provider-owned shared item")
        self.assertIsNone(inbox_ingest.check_ownership(
            self.repo, "chatgpt", "cloud", "memories/owned.md"))


class ReportBadgeTests(BrainRepo, unittest.TestCase):
    """`report_build` builds the human-facing report, badging each entry
    shared/private. It walks `skills/` too — so it needed the same resolver
    carve-out as the packet and the surface, and did not get it in the first
    pass: the flip would have silently turned every skill in the brain private
    and dropped it from the shareable count."""

    def entries(self):
        return {e["name"]: e for e in report_build.load_entries(self.repo)}

    def test_skill_is_still_badged_shared(self):
        """MUTATION: in `report_build.load_entries`, change the skills loop's
        `"shared": True,` back to `"shared": visibility_from_text(text) == "shared",`."""
        self.assertTrue(self.entries()["a-skill"]["shared"])

    def test_unmarked_item_is_badged_private(self):
        """MUTATION: in `report_build.visibility_from_text`, restore the old
        default with `return "shared" if seen in (None, "shared") else "private"`."""
        self.item("never-classified")
        self.write_index()
        self.commit("add an unclassified item")
        e = self.entries()
        self.assertFalse(e["never-classified"]["shared"])
        self.assertTrue(e["shared-one"]["shared"])


class MigrationPathTests(unittest.TestCase):
    """`docs/visibility-design.md` §6 now names
    `loreport_change_memory_settings` as the way to clear the gate's list. That
    is a promise about an item with NO `visibility:` line, so it is worth
    proving rather than assuming — a documented remedy that cannot actually
    insert the field would leave a brain permanently unable to publish."""

    def test_setting_visibility_on_an_unmarked_item_round_trips(self):
        """MUTATION: in `mcp_server._set_visibility_field`, delete the
        `if not replaced: new_fm_lines.append(...)` insertion — updating an
        already-marked item still works, so only this case catches it."""
        text = UNMARKED.format(name="never-classified")
        self.assertEqual(mcp_server._visibility_from_text(text), "local")
        for target in ("shared", "local"):
            out = mcp_server._set_visibility_field(text, target)
            self.assertEqual(mcp_server._visibility_from_text(out), target)
            self.assertTrue(brain_merge._has_explicit_visibility(out))
            self.assertIn("Body of never-classified.", out)


if __name__ == "__main__":
    unittest.main()
