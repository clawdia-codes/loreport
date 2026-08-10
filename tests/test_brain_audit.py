#!/usr/bin/env python3
"""Tests for hub/brain_audit.py — the aimable classification + leak audit.

Every test here exists to redden one specific production line; the mutation is
named in the test's own comment, and each was actually run against the code.
Two of them carry the weight:

  * `test_empty_packet_fails_even_when_every_item_is_local` — the vacuity guard.
    A safety assertion that iterates a collection is vacuously true when the
    collection is empty, and this repo has already shipped that exact bug: the
    published-packet privacy check passed on an EMPTY packet. The guard must be
    UNCONDITIONAL, so this fixture is the case a `if shared_items_exist:` wrapper
    would sail past — a brain whose items are all local, where the empty packet
    is "legitimate" and the check still has nothing to certify.

  * `test_quoted_visibility_local_in_a_surface_is_a_leak` — the divergence. The
    repo holds two visibility rules: fail-closed parsing (5 copies) and a
    whole-line `^visibility:\\s*local\\s*$` match (hub/project.py,
    make-surface.sh, doctor.sh). `visibility: "local"` is local to the first and
    NOT local to the second, so the producer keeps it in the paste-into-cloud
    surface and the old checker — which used the producer's rule — reported
    green. Swap this checker to the whole-line rule and that test goes green on a
    real leak, which is the proof it is stronger than what shipped.

Fixtures are real git repos in tempfile with a real `main`, because the audit
reads item frontmatter through `git show main:<path>` on purpose and a
dict-level fake would not exercise that.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hub"))

import brain_audit  # noqa: E402


def _item(name, visibility="visibility: shared", domain="domain: personal", typ="project"):
    vis = f"{visibility}\n" if visibility else ""
    dom = f"{domain}\n" if domain else ""
    return (
        "---\n"
        f"name: {name}\n"
        f"description: hook for {name}\n"
        f"type: {typ}\n"
        f"{vis}{dom}"
        "---\n\n"
        f"Body of {name}.\n"
    )


class BrainFixture:
    """A minimal but REAL brain: a git repo with main, items committed there, and
    the three published surfaces on disk (two of which are gitignored in a real
    brain and therefore only ever exist on disk)."""

    def __init__(self, items):
        self.dir = tempfile.mkdtemp(prefix="my-brain-audit-")
        subprocess.run(["git", "init", "-q", "-b", "main", self.dir], check=True)
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "test")
        os.makedirs(os.path.join(self.dir, "memories"))
        self.items = dict(items)
        for name, text in self.items.items():
            self.write(f"memories/{name}.md", text)
        self.write("INDEX.md", self.index_text(self.items))
        self._git("add", "-A")
        self._git("commit", "-qm", "seed")
        # Published surfaces: catalogue every item the fail-closed rule calls
        # shared. This is what a correct publish run would have produced.
        published = self.index_text(
            {n: t for n, t in self.items.items()
             if brain_audit.effective_visibility(t) == "shared"})
        for rel in brain_audit.PUBLISHED_SURFACES:
            self.write(rel, published)

    def _git(self, *args):
        subprocess.run(["git", "-C", self.dir] + list(args),
                       capture_output=True, text=True, check=True)

    @staticmethod
    def index_text(items):
        lines = ["# INDEX", "", "## Memories", ""]
        lines += [f"- [[{n}]] — hook for {n}" for n in sorted(items)]
        return "\n".join(lines) + "\n"

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def commit_item(self, name, text):
        """Change an item ON MAIN — the audit reads frontmatter from main, so a
        worktree-only edit would (correctly) not be seen."""
        self.write(f"memories/{name}.md", text)
        self._git("add", "-A")
        self._git("commit", "-qm", f"update {name}")

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.brain = BrainFixture({
            "alpha-note": _item("alpha-note"),
            "beta-note": _item("beta-note"),
            "secret-note": _item("secret-note", visibility="visibility: local"),
        })
        self.addCleanup(self.brain.cleanup)

    def run_audit(self):
        return brain_audit.audit(self.brain.dir)

    def severities(self, findings):
        return [f.severity for f in findings]

    def messages(self, findings):
        return "\n".join(f.message for f in findings)

    # --- positive control ----------------------------------------------------

    def test_correct_brain_passes_and_actually_looked_at_something(self):
        """Without this, every red below could be red for the wrong reason. It
        also pins the counts, so a check that silently stops reading items can't
        pass by finding nothing.

        Mutation run: in `audit`, `item_paths.sort()` -> `item_paths = []`.
        Observed: 6 failed, 5 passed — this test plus every test that depends on
        an item being read at all (both classification tests, the quoted-local
        leak, the empty-packet guard, and the exit-0 contract).
        """
        findings, counts = self.run_audit()
        self.assertEqual(findings, [], self.messages(findings))
        self.assertEqual(counts["items"], 3)
        self.assertEqual((counts["shared"], counts["local"]), (2, 1))
        self.assertEqual(counts["reach_checked"], 2)
        self.assertEqual(counts["surface_refs"]["hub/published/packet.md"], 2)

    # --- classification ------------------------------------------------------

    def test_item_without_explicit_visibility_is_a_finding(self):
        """Absent `visibility:` means SHARED — the item is cloud-published by
        omission. That is the 2026-08-07 leak, so it must be reported even though
        the file is otherwise well-formed and nothing has escaped yet.

        Mutation run: in `visibility_problem`, neutralise
        `if "visibility" not in fields: return ...` (body -> `pass`).
        Observed: 1 failed, 10 passed — this test alone.
        """
        self.brain.commit_item("alpha-note", _item("alpha-note", visibility=""))
        findings, _ = self.run_audit()
        self.assertIn("RISK", self.severities(findings))
        self.assertIn("no explicit `visibility:`", self.messages(findings))

    def test_item_without_explicit_domain_is_a_finding(self):
        """`domain` grants and revokes nothing, so this is reported at META, not
        LEAK — but it is still a nonzero exit, because an unclassified item is
        exactly what nobody was tracking.

        Mutation run: in `domain_problem`, `if seen is None: return ...` ->
        `if seen is None: return None`.
        Observed: 1 failed, 10 passed — this test alone.
        """
        self.brain.commit_item("beta-note", _item("beta-note", domain=""))
        findings, _ = self.run_audit()
        self.assertIn("META", self.severities(findings))
        self.assertIn("no explicit `domain:`", self.messages(findings))

    # --- leaks ---------------------------------------------------------------

    def test_local_item_in_the_published_packet_is_a_leak(self):
        """
        Mutation run: in `audit`, `if effective_visibility(item_text) == "local":`
        -> `if False:` (the packet/surface leak branch).
        Observed: 3 failed, 8 passed — this test and both leak tests below
        (paste-surface and quoted-local). Nothing else moved, so the leak
        assertions are the only thing that branch is holding up.
        """
        self.brain.write("hub/published/packet.md",
                         BrainFixture.index_text(self.brain.items))
        findings, _ = self.run_audit()
        self.assertIn("LEAK", self.severities(findings))
        self.assertIn("[[secret-note]]", self.messages(findings))
        self.assertIn("hub/published/packet.md", self.messages(findings))

    def test_local_item_in_a_paste_surface_is_a_leak(self):
        """The packet is not the only egress. hub/surface-*.md are what a human
        pastes into a web chat, and guarding only the packet would leave that
        half unwatched — which is how the projection/publish divergence stayed
        invisible.

        Mutation run: `PUBLISHED_SURFACES` -> `("hub/published/packet.md",)`.
        Observed: 3 failed, 8 passed — this test, the quoted-local leak, and the
        missing-surface blind-spot test. `test_local_item_in_the_published_packet_is_a_leak`
        stayed GREEN throughout, which is exactly the half-blind state being
        guarded against: the packet looks watched while the paste path is not.
        """
        self.brain.write("hub/surface-claude-ai.md",
                         BrainFixture.index_text(self.brain.items))
        findings, _ = self.run_audit()
        leaks = [f for f in findings if f.severity == "LEAK"]
        self.assertEqual(len(leaks), 1, self.messages(findings))
        self.assertIn("hub/surface-claude-ai.md", leaks[0].message)

    def test_quoted_visibility_local_in_a_surface_is_a_leak(self):
        """THE DIVERGENCE. `visibility: "local"` is local to the fail-closed
        parser and NOT local to the whole-line rule in hub/project.py /
        make-surface.sh / doctor.sh — so the projector keeps the item in the
        paste surface while snapshot_publish drops it from the packet, and the
        old checker, sharing the producer's rule, called that green.

        Mutation run: `effective_visibility` body replaced with
        `return "local" if re.search(r"(?mi)^visibility:\\s*local\\s*$", text) else "shared"`
        (hub/project.py's rule).
        Observed: 1 failed, 10 passed — ONLY this test. The quoted-local item was
        reported as shared, exactly reproducing the green-on-a-real-leak
        behaviour, while both other leak tests stayed green. That isolation is
        the point: no other test in this file distinguishes the two rules.
        """
        self.brain.commit_item(
            "secret-note", _item("secret-note", visibility='visibility: "local"'))
        # A projector using the whole-line rule leaves it in the surface.
        self.brain.write("hub/surface-chatgpt.md",
                         BrainFixture.index_text(self.brain.items))
        findings, _ = self.run_audit()
        leaks = [f for f in findings if f.severity == "LEAK"]
        self.assertEqual(len(leaks), 1, self.messages(findings))
        self.assertIn("[[secret-note]]", leaks[0].message)
        # …and the non-canonical spelling is itself reported, before it leaks.
        self.assertIn("not in canonical form", self.messages(findings))

    # --- vacuity -------------------------------------------------------------

    def test_empty_packet_fails_even_when_every_item_is_local(self):
        """THE VACUITY GUARD, in the form that a conditional guard would miss.

        Every item here is local, so an empty packet is "legitimate" and a check
        gated on `if shared_items_exist:` would report green — having asserted
        nothing whatsoever about the surface it exists to watch. The guard must
        be unconditional.

        Mutation run: in `audit`, `if not catalog:` -> `if False:` (the BLIND
        guard). Observed: 1 failed, 10 passed — this test alone; the audit
        returned zero findings and exit 0 on a brain whose three published
        surfaces contained no items at all.
        """
        brain = BrainFixture({
            "one-note": _item("one-note", visibility="visibility: local"),
            "two-note": _item("two-note", visibility="visibility: local"),
        })
        self.addCleanup(brain.cleanup)
        for rel in brain_audit.PUBLISHED_SURFACES:
            brain.write(rel, "# INDEX\n\n(nothing to publish)\n")

        findings, counts = brain_audit.audit(brain.dir)
        self.assertEqual(counts["shared"], 0)
        blind = [f for f in findings if f.severity == "BLIND"]
        self.assertEqual(len(blind), len(brain_audit.PUBLISHED_SURFACES),
                         self.messages(findings))
        self.assertEqual(brain_audit.main(["--brain-dir", brain.dir]), 1)

    def test_missing_surface_is_a_blind_spot_not_a_pass(self):
        """A surface that isn't there did not get checked. Reporting that as
        clean is the same failure as reporting an empty one clean.

        Mutation run: in `audit`, drop the `add("BLIND", ...)` from the
        `if not os.path.isfile(path):` branch, leaving the bare `continue`.
        Observed: 1 failed, 10 passed — this test alone; the audit reported a
        clean brain while one of its three egress surfaces was simply absent.
        """
        os.remove(os.path.join(self.brain.dir, "hub/surface-chatgpt.md"))
        findings, _ = self.run_audit()
        self.assertIn("BLIND", self.severities(findings))
        self.assertIn("hub/surface-chatgpt.md", self.messages(findings))

    # --- reconciliation ------------------------------------------------------

    def test_shared_item_missing_from_the_packet_is_a_finding(self):
        """The other direction of the same wall: a shared item catalogued on main
        but absent from the packet means cloud providers silently lost reach to
        it — the archive-seam failure, which has no error anywhere else.

        Mutation run: in `audit`, `if name not in packet_names:` -> `if False:`.
        Observed: 1 failed, 10 passed — this test alone.
        """
        self.brain.write(
            "hub/published/packet.md",
            BrainFixture.index_text({"alpha-note": self.brain.items["alpha-note"]}))
        findings, _ = self.run_audit()
        self.assertIn("[[beta-note]]", self.messages(findings))
        self.assertIn("absent", self.messages(findings))

    # --- exit-code contract --------------------------------------------------

    def test_unauditable_brain_exits_2_and_never_0(self):
        """`main` unresolvable must never read as "clean". It also must not read
        as "found problems": 2 says the instrument could not run, which is a
        different repair.

        Mutation run: in `audit`, `if main_sha is None: raise CannotAudit(...)`
        -> `main_sha = "unknown"` with the raise disabled.
        Observed: 1 failed, 10 passed — this test alone; main() returned 1 (a
        findings exit) on a directory that is not a git repo at all.
        """
        empty = tempfile.mkdtemp(prefix="my-brain-not-a-repo-")
        self.addCleanup(shutil.rmtree, empty, True)
        self.assertEqual(brain_audit.main(["--brain-dir", empty]), 2)
        with self.assertRaises(brain_audit.CannotAudit):
            brain_audit.audit(empty)

    def test_clean_brain_exits_0(self):
        """The pair to the above: the exit codes must be distinguishable in all
        three directions, since a driver script and a human both read them.

        Mutation run: in `main`, `return 1 if findings else 0` -> `return 0`.
        Observed: 1 failed, 10 passed — the failure was
        `test_empty_packet_fails_even_when_every_item_is_local` (a brain with an
        empty packet exited 0). This test stays green under that mutation by
        construction, which is why the exit-1 assertion lives over there.
        """
        self.assertEqual(brain_audit.main(["--brain-dir", self.brain.dir]), 0)


class DriverScriptTests(unittest.TestCase):
    """scripts/loreport-audit — the part that makes this check AIMABLE, which is
    precisely what brain-template/doctor.sh lacks (`cd "$(dirname "$0")"` makes
    its own directory the brain, full stop). If the config plumbing is wrong the
    Python above is unreachable in practice, so it gets its own tests."""

    ENGINE = os.path.dirname(HERE)
    SCRIPT = os.path.join(ENGINE, "scripts", "loreport-audit")

    def setUp(self):
        self.brain = BrainFixture({"alpha-note": _item("alpha-note")})
        self.addCleanup(self.brain.cleanup)
        self.conf = os.path.join(self.brain.dir, "loreport.conf")
        with open(self.conf, "w", encoding="utf-8") as fh:
            fh.write(f"LOREPORT_BRAIN={self.brain.dir}\nLOREPORT_ENGINE={self.ENGINE}\n")

    def _run(self, *args, timeout=60):
        return subprocess.run(["bash", self.SCRIPT] + list(args),
                              capture_output=True, text=True, timeout=timeout)

    def test_config_aims_the_audit_at_another_directory_entirely(self):
        """The whole point: run from anywhere, audit the brain named in the
        config — not the script's own directory.

        Mutation run: in scripts/loreport-audit, `--brain-dir "$BRAIN"` ->
        `--brain-dir "$(dirname "$0")"` (doctor.sh's rule).
        Observed: this test failed — exit 2, "cannot resolve `main`", because the
        engine checkout's scripts/ directory is not a brain.
        """
        r = self._run("--config", self.conf)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.brain.dir, r.stdout)
        self.assertIn("PASS", r.stdout)

    def test_findings_exit_1_and_a_bad_config_exits_2(self):
        """A leak check whose failure is indistinguishable from a broken
        invocation is not a check. 1 = found something, 2 = could not look.

        Mutation run: in scripts/loreport-audit, the `[ ! -r "$CONFIG" ]` gate
        -> `CONFIG=/dev/null` fallback instead of `exit 2`.
        Observed: 1 failed, 13 passed — this test; a nonexistent config exited 1,
        i.e. it claimed to have audited a brain and found problems.
        """
        self.brain.write("hub/published/packet.md",
                         BrainFixture.index_text(self.brain.items))
        self.brain.commit_item("alpha-note", _item("alpha-note", visibility=""))
        self.assertEqual(self._run("--config", self.conf).returncode, 1)
        self.assertEqual(self._run("--config", self.conf + ".nope").returncode, 2)

    def test_config_missing_a_required_key_exits_2_not_1(self):
        """Found while testing the above, and fixed here rather than copied: the
        idiomatic `${LOREPORT_BRAIN:?...}` used by loreport-health and
        loreport-sync aborts a *script* with exit 1 on this bash (127 is the
        `bash -c` case), so a config missing the brain path would land on this
        script's "found problems" code. A wiring error must not be able to
        impersonate a finding.

        Mutation run: in scripts/loreport-audit, the two explicit `[ -n ... ] ||
        exit 2` guards -> `BRAIN="${LOREPORT_BRAIN:?...}"` /
        `FRAMEWORK="${LOREPORT_ENGINE:?...}"`.
        Observed: 1 failed, 14 passed — this test; exit was 1, not 2.
        """
        halfconf = os.path.join(self.brain.dir, "half.conf")
        with open(halfconf, "w", encoding="utf-8") as fh:
            fh.write(f"LOREPORT_ENGINE={self.ENGINE}\n")
        r = self._run("--config", halfconf)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("LOREPORT_BRAIN not set", r.stderr)

    def test_trailing_config_with_no_value_exits_instead_of_hanging(self):
        """Verified bug in both sibling scripts: `shift 2 || true` with one
        argument left shifts NOTHING and swallows the failure, so $1 stays
        "--config" and the loop spins forever — `timeout 5 bash
        scripts/loreport-health --config` exits 124. Under a timer that is a hung
        unit, which is strictly worse than a failed one. This driver must not
        inherit it.

        Mutation run: in scripts/loreport-audit, the guarded `--config` case ->
        `--config) CONFIG="${2:-}"; shift 2 || true ;;` (the sibling form).
        Observed: this test failed with subprocess.TimeoutExpired after 10s —
        the script never terminated.
        """
        r = self._run("--config", timeout=10)
        self.assertEqual(r.returncode, 2)
        self.assertIn("needs a value", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
