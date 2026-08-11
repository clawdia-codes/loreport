#!/usr/bin/env python3
"""Replay of every capture that a real brain quarantined, 2026-08-04..08-07.

Eleven quarantine events, six of them on one day, and the digest's own postmortem
counted "five of five parse-errors have been this one shape". Nothing replayed
them: the shapes were described in prose, in a private repo, and the engine's
tests never saw a single real malformed block. So the same emit failed five more
times, and two artifacts were 0 bytes with no explanation anyone could give from
the digest alone.

`tests/fixtures/quarantine/` holds a structure-exact reproduction of each
artifact (see the README there for what is preserved and what is redacted), and
this file runs every one of them through the real CLI against a real two-branch
git brain — not through parse_block in isolation, because the property that
matters is what ends up committed, and a "recovered" capture that commits a body
whose frontmatter no parser can read would pass a parse-level assertion while
being worse than the quarantine it replaced.

The other half of the file is the refusals. An inference that fills in a missing
`file=` from the block's own `name:` is only safe while it CANNOT invent a name
it did not read — the failure mode is a capture silently landing at
`memories/mpb-capture-9x7u7u37.md`, or at `../../etc/passwd`. Each refusal here
is a separate test because each is a separate way to be wrong.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "hub"))

import inbox_ingest  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "quarantine")
INGEST = os.path.join(REPO, "hub", "inbox_ingest.py")
PROVIDER = "claude"
BRANCH = f"provider/{PROVIDER}"

# What each archived artifact must do once the engine stops losing it. The two
# 0-byte ones stay quarantined — an empty input is not recoverable, it is only
# diagnosable — but under a reason of their own.
COMMITTED = {
    "2026-08-04-mpb-capture-gqkezjtb.txt": "memories/project-alpha-pipeline.md",
    "2026-08-04-mpb-capture-4pa3swhb.txt": "memories/project-beta-followup-pending.md",
    "2026-08-05-mpb-capture-j98m523j.txt": "memories/project-alpha-phase1-shipped-2026-08-05.md",
    "2026-08-06-mpb-capture-mc4tz2cn.txt": "memories/feedback-avoid-display-commands.md",
    "2026-08-07-mpb-capture-9x7u7u37.txt": "memories/decision-defer-bulk-import.md",
    "2026-08-07-mpb-capture-n6rp9k4c.txt": "memories/feedback-use-absolute-paths.md",
}
EMPTY = [
    "2026-08-07-mpb-capture-kfcadatl.txt",
    "2026-08-07-mpb-capture-rit_ofwc.txt",
]


class _Brain(unittest.TestCase):
    """A two-branch brain (main + provider/claude) with real git history."""

    def setUp(self):
        self.brain = tempfile.mkdtemp(prefix="loreport-capture-")
        self.addCleanup(shutil.rmtree, self.brain, True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.brain], check=True)
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "test")
        os.makedirs(os.path.join(self.brain, "memories"))
        with open(os.path.join(self.brain, "memories", ".keep"), "w") as fh:
            fh.write("")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")
        self.git("branch", BRANCH)

    def git(self, *args, check=True):
        return subprocess.run(["git", "-C", self.brain] + list(args),
                              capture_output=True, text=True, check=check)

    def ingest_text(self, text, trust="cloud"):
        """Run the real CLI on `text`. Returns (returncode, stdout+stderr)."""
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="mpb-capture-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        r = subprocess.run(
            [sys.executable, INGEST, PROVIDER, path, "--brain-dir", self.brain,
             "--trust", trust],
            capture_output=True, text=True,
        )
        return r.returncode, r.stdout + r.stderr

    def ingest_fixture(self, name):
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
            return self.ingest_text(fh.read())

    def on_branch(self, rel_path):
        """The committed bytes of `rel_path` on provider/claude, or None."""
        r = self.git("show", f"{BRANCH}:{rel_path}", check=False)
        return r.stdout if r.returncode == 0 else None

    def branch_files(self):
        return set(self.git("ls-tree", "-r", "--name-only", BRANCH).stdout.split())

    def digest(self):
        path = os.path.join(self.brain, "hub", "quarantine", "digest.md")
        if not os.path.isfile(path):
            return ""
        with open(path, encoding="utf-8") as fh:
            return fh.read()


class ArchivedCorpusReplay(_Brain):
    """The eight real artifacts, through the real gate chain."""

    def test_every_recoverable_artifact_now_commits(self):
        for name, expected_path in sorted(COMMITTED.items()):
            with self.subTest(artifact=name):
                rc, out = self.ingest_fixture(name)
                self.assertEqual(rc, 0, f"{name} still quarantines: {out}")
                self.assertIsNotNone(
                    self.on_branch(expected_path),
                    f"{name} committed nothing at {expected_path}: {out}")

    def test_recovered_bodies_are_readable_frontmatter(self):
        """The repair has to leave a file the REST of the pipeline can parse.

        Four of these artifacts lost their opening `---` in the same emit that
        lost the tag attributes. Inferring the path but committing the body
        verbatim would swap a loud quarantine for a silent one: an item on a
        provider branch that brain_merge cannot read frontmatter from, so it
        joins no INDEX and no visibility check ever classifies it."""
        for name, expected_path in sorted(COMMITTED.items()):
            with self.subTest(artifact=name):
                self.ingest_fixture(name)
                text = self.on_branch(expected_path) or ""
                fm, _ = inbox_ingest.parse_frontmatter(text)
                self.assertIsNotNone(fm, f"{name}: committed body has no parseable frontmatter")
                self.assertEqual(fm.get("name"),
                                 os.path.basename(expected_path)[:-3])
                for key in ("description", "type", "visibility"):
                    self.assertIn(key, fm, f"{name}: {key} lost in the repair")

    def test_repair_preserves_the_whole_body(self):
        """Only delimiters may be added — no prose may be dropped."""
        name = "2026-08-04-mpb-capture-gqkezjtb.txt"
        self.ingest_fixture(name)
        committed = self.on_branch(COMMITTED[name])
        with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
            original = fh.read()
        for line in original.split("\n"):
            line = line.strip()
            if line and not line.startswith("<MEMORY") and line != "</MEMORY>":
                self.assertIn(line, committed, f"line dropped by the repair: {line[:60]}")

    def test_empty_artifacts_are_quarantined_under_their_own_reason(self):
        """The 0-byte mystery, pinned.

        These two are 0 bytes because quarantine() copies its input verbatim and
        the input WAS empty — the MCP tool wrote a temp file from a `block`
        argument that was never supplied. They were filed as `parse-error: no
        <MEMORY …> block found`, the same words a malformed block gets, which is
        why "a parse failure writes the raw block, so the file has content"
        could not be reconciled with a 0-byte file for three days. One reason
        must not cover both conditions."""
        for name in EMPTY:
            with self.subTest(artifact=name):
                rc, out = self.ingest_fixture(name)
                self.assertEqual(rc, 1)
                self.assertIn("empty-block", out)
        digest = self.digest()
        self.assertIn("- reason: empty-block", digest)
        self.assertNotIn("- reason: parse-error", digest)
        qdir = os.path.join(self.brain, "hub", "quarantine", PROVIDER)
        sizes = [os.path.getsize(os.path.join(qdir, f)) for f in os.listdir(qdir)]
        self.assertEqual(sizes, [0, 0],
                         "an empty input must still produce the 0-byte artifact it produced live")


class InferenceRefusals(_Brain):
    """Everything the recovery path must NOT do."""

    HEADED = ('<MEMORY>\n---\nname: {name}\ndescription: d\ntype: {typ}\n'
              'visibility: shared\n---\nbody\n</MEMORY>\n')

    def test_a_name_it_cannot_read_is_never_invented(self):
        """A block whose frontmatter states everything EXCEPT `name:` is the one
        shape where an invented name would actually reach a commit — the schema
        gate would pass it. So the refusal has to happen here, by name, and not
        fall back to the block file (`mpb-capture-<random>.txt`, which would
        land as `memories/mpb-capture-<random>.md`); that fallback is
        structurally impossible only for as long as parse_block is never handed
        the path, which is why the message is asserted and not just the rc."""
        rc, out = self.ingest_text(
            '<MEMORY>\n---\ndescription: d\ntype: project\nvisibility: shared\n'
            '---\nbody\n</MEMORY>\n')
        self.assertEqual(rc, 1)
        self.assertIn("no `name:`", out)
        self.assertEqual(self.branch_files(), {"memories/.keep"},
                         "a block with no readable name committed something anyway")
        self.assertNotIn("mpb-capture", " ".join(sorted(self.branch_files())))

    def test_a_name_that_is_not_a_slug_is_refused(self):
        """`name:` is interpolated straight into a path. A traversal name must
        die at the inference, not merely at the allowlist one gate later."""
        rc, out = self.ingest_text(self.HEADED.format(name="../../etc/passwd", typ="project"))
        self.assertEqual(rc, 1)
        self.assertIn("kebab-case", out)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.brain), "etc")))
        self.assertEqual(self.branch_files(), {"memories/.keep"})

    def test_an_unknown_type_gets_no_folder_guess(self):
        rc, out = self.ingest_text(self.HEADED.format(name="thing", typ="notatype"))
        self.assertEqual(rc, 1)
        self.assertIn("not a known item type", out)
        self.assertEqual(self.branch_files(), {"memories/.keep"})

    def test_knowledge_goes_to_the_knowledge_folder(self):
        rc, out = self.ingest_text(self.HEADED.format(name="topic-page", typ="knowledge"))
        self.assertEqual(rc, 0, out)
        self.assertIsNotNone(self.on_branch("knowledge/topic-page.md"))

    def test_delete_is_never_inferred(self):
        """An absent action must not resolve to the one action that destroys an
        item — an emitter that drops attributes has said nothing about intent."""
        self.ingest_text(self.HEADED.format(name="victim", typ="project"))
        self.assertIsNotNone(self.on_branch("memories/victim.md"))
        rc, out = self.ingest_text(
            '<MEMORY file="memories/victim.md">\n---\nname: victim\ndescription: d\n'
            'type: project\nvisibility: shared\n---\nreplacement body\n</MEMORY>\n',
            trust="local")
        self.assertEqual(rc, 0, out)
        self.assertIn("replacement body", self.on_branch("memories/victim.md"))

    def test_prose_is_not_swallowed_as_frontmatter(self):
        """The delimiter repair reads leading `key: value` lines, and one of the
        real artifacts opens its prose with `Continuation doc: /srv/…` — a line
        that matches that shape exactly. Only the schema's own key vocabulary
        separates the two, so a body where such a line follows real keys must be
        refused whole rather than have prose promoted into frontmatter."""
        for label, original in (
            # `Continuation doc:` — two words, so only the key REGEX rejects it.
            ("multi-word prose key",
             "name: x\ntype: project\nContinuation doc: /srv/example/HANDOFF.md\n\nprose\n"),
            # `Note:` — a single word followed by a colon is indistinguishable
            # from a frontmatter key by shape alone, so only the schema's key
            # VOCABULARY rejects it. Both guards are load-bearing.
            ("single-word prose key",
             "name: x\ntype: project\nNote: this sentence is body text\n---\nprose\n"),
            ("no leading keys at all",
             "Continuation doc: /srv/example/HANDOFF.md\n\nprose\n"),
        ):
            with self.subTest(shape=label):
                self.assertEqual(inbox_ingest._repair_headless_frontmatter(original),
                                 (original, False))

    def test_a_well_formed_tag_keeps_the_strict_frontmatter_rule(self):
        """The repair is scoped to blocks whose tag was already malformed. A
        block that got the grammar right and the frontmatter wrong is still a
        schema error — widening the repair to those would make a body-level
        malformation undetectable everywhere."""
        rc, out = self.ingest_text(
            '<MEMORY file="memories/x.md" action="new">\nname: x\ntype: project\n'
            'description: d\nvisibility: shared\n---\nbody\n</MEMORY>\n')
        self.assertEqual(rc, 1)
        self.assertIn("schema-invalid", out)


class MissingIndexLine(_Brain):
    def test_a_block_without_an_index_line_commits(self):
        """`INDEX:` had no consumer: the parsed value is read nowhere in hub/,
        and INDEX.md is rebuilt from item frontmatter by brain_merge. Discarding
        a whole capture over a missing one cost content and bought no signal."""
        rc, out = self.ingest_text(
            '<MEMORY file="memories/no-index.md" action="new">\n---\nname: no-index\n'
            'description: d\ntype: project\nvisibility: shared\n---\nbody\n</MEMORY>\n')
        self.assertEqual(rc, 0, out)
        self.assertIsNotNone(self.on_branch("memories/no-index.md"))
        self.assertEqual(self.digest(), "", "nothing should have been quarantined")


class TheRecoveryLeavesADurableRecord(_Brain):
    """A repaired capture must not be byte-indistinguishable from a correct one.

    The recovery path does two things the emitter did not: it SYNTHESIZES
    `file`/`action` from frontmatter, and it REWRITES the committed body by
    inserting `---` delimiters. Before this, the only record of either was a
    stdout line in inbox_ingest.main() — the commit trailers were
    Provider/Trust/Action/File/Ingested-At and nothing more. Worse, the SWEEP
    (hub/sweep_run.py, the nightly bulk path the four real bare-tag emits
    actually came from) prints nothing and returned the bare string "committed",
    which is what lands in its report and in its state ledger. So in production
    the FREQUENT case had no record anywhere.
    """

    BARE = ('<MEMORY>\n---\nname: recovered\ndescription: d\ntype: project\n'
            'visibility: shared\n---\nbody\n</MEMORY>\n')

    def head_message(self):
        return self.git("log", "-1", "--format=%B", BRANCH).stdout

    def test_an_inferred_capture_is_marked_in_the_commit_itself(self):
        """Mutation: delete the `if block.get("inferred"): msg += ...` lines
        from commit_block in hub/inbox_ingest.py.

        `git log` is the durable record; stdout is not one."""
        rc, out = self.ingest_text(self.BARE)
        self.assertEqual(rc, 0, out)
        body = self.head_message()
        self.assertIn("Inferred:", body)
        self.assertIn("file", body)
        self.assertIn("action", body)

    def test_a_repaired_body_says_which_repair_happened(self):
        """Mutation: as above.

        Headless frontmatter is repaired by inserting `---` lines the emitter
        never sent — the committed BYTES differ from what was submitted, which
        is the change most worth being able to find later."""
        rc, out = self.ingest_text(
            '<MEMORY action="new" file="memories/headless.md">\n'
            'name: headless\ndescription: d\ntype: project\nvisibility: shared\n'
            '\nbody\n</MEMORY>\n')
        self.assertEqual(rc, 0, out)
        self.assertIn("frontmatter delimiters", self.head_message())

    def test_a_correctly_authored_capture_carries_no_such_trailer(self):
        """Mutation: emit the trailer unconditionally.

        A marker on every commit is a marker on none — `git log
        --grep='^Inferred:'` has to answer "how often is the emitter actually
        losing the grammar?", which is the question that decides whether this
        recovery path can ever be retired."""
        rc, out = self.ingest_text(
            '<MEMORY file="memories/clean.md" action="new">\n---\nname: clean\n'
            'description: d\ntype: project\nvisibility: shared\n---\nbody\n'
            '</MEMORY>\n')
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Inferred:", self.head_message())

    def test_the_sweep_reports_the_recovery_too(self):
        """Mutation: `return "committed"` unconditionally at the end of
        sweep_run.process_candidate.

        The sweep is the nightly bulk producer and prints no INFERRED line, so
        its outcome string is the only thing its report and its state ledger
        ever see. Also pins the counter: `summary["committed"]` keys on the
        outcome, so a richer string must not stop counting as a capture."""
        sys.path.insert(0, os.path.join(REPO, "hub"))
        import sweep_run  # noqa: E402

        block_path = os.path.join(self.brain, "cand.txt")
        with open(block_path, "w", encoding="utf-8") as fh:
            fh.write(self.BARE)
        outcome = sweep_run.process_candidate(
            self.brain, {"provider": PROVIDER, "block": self.BARE},
            block_path, "local")
        self.assertTrue(outcome.startswith("committed"), outcome)
        self.assertIn("inferred", outcome)
        self.assertIn("file", outcome)
        # And it still COUNTS as a capture. The nightly summary keys on the
        # outcome string, so an `==` compare would have silently under-reported
        # exactly the class this suffix exists to make visible.
        self.assertEqual(sweep_run.classify(outcome), "committed")

    def test_the_summary_still_counts_an_inferred_capture_as_a_capture(self):
        """Mutation: `if outcome.startswith("committed")` -> `if outcome ==
        "committed"` in sweep_run.classify."""
        sys.path.insert(0, os.path.join(REPO, "hub"))
        import sweep_run  # noqa: E402
        self.assertEqual(
            sweep_run.classify("committed (inferred: file, action)"), "committed")
        self.assertEqual(sweep_run.classify("committed"), "committed")
        self.assertEqual(sweep_run.classify("skipped: no change"), "no_change")
        self.assertEqual(
            sweep_run.classify("quarantined: secret-scan"), "quarantined")
        self.assertIsNone(sweep_run.classify("dry-run: would offer"))


class McpMissingBlockArgument(unittest.TestCase):
    """The 0-byte artifacts' upstream cause: the MCP tool declares `block`
    required, and then defaulted it to "" when absent, manufacturing an empty
    capture instead of rejecting the call."""

    def setUp(self):
        sys.path.insert(0, os.path.join(REPO, "hub"))
        import mcp_server  # noqa: E402
        self.mcp = mcp_server
        self.brain = tempfile.mkdtemp(prefix="loreport-mcp-")
        self.addCleanup(shutil.rmtree, self.brain, True)

        self.calls = []
        real_map = dict(mcp_server.CREDENTIAL_PROVIDER_MAP)
        real_trust = dict(mcp_server.CREDENTIAL_TRUST_MAP)
        real_save = mcp_server.tool_loreport_save_memory
        mcp_server.CREDENTIAL_PROVIDER_MAP = {"tok": PROVIDER}
        mcp_server.CREDENTIAL_TRUST_MAP = {"tok": "local"}
        mcp_server.tool_loreport_save_memory = lambda *a, **k: self.calls.append(a) or {}

        def restore():
            mcp_server.CREDENTIAL_PROVIDER_MAP = real_map
            mcp_server.CREDENTIAL_TRUST_MAP = real_trust
            mcp_server.tool_loreport_save_memory = real_save
        self.addCleanup(restore)

    def test_absent_block_argument_is_refused_not_captured(self):
        result = self.mcp.dispatch(self.brain, "tok", "loreport_save_memory", {})
        self.assertIn("error", result)
        self.assertEqual(self.calls, [], "an absent block reached the capture path")

    def test_blank_block_argument_is_refused_not_captured(self):
        result = self.mcp.dispatch(self.brain, "tok", "loreport_save_memory",
                                   {"block": "   \n"})
        self.assertIn("error", result)
        self.assertEqual(self.calls, [])

    def test_a_real_block_still_reaches_the_capture_path(self):
        self.mcp.dispatch(self.brain, "tok", "loreport_save_memory",
                          {"block": "<MEMORY>x</MEMORY>"})
        self.assertEqual(len(self.calls), 1)

    def test_the_refusal_is_reported_as_an_error_to_the_client(self):
        """isError is what makes the caller retry instead of believing the
        memory was saved."""
        raw = self.mcp.dispatch(self.brain, "tok", "loreport_save_memory", {})
        self.assertTrue(self.mcp._as_tool_result(raw)["isError"])


if __name__ == "__main__":
    unittest.main()
