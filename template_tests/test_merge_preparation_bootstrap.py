"""Initial trust admission is separate from normal signed PR preparation.

GitHub transport, collision lookup and CI results are fixture inputs here.
Git trees, approvals, version updates, signatures and restoration are real.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from template_tests import test_merge_preparation as preparation_tests
from template_tests import test_template

ROOT = preparation_tests.ROOT


QUALITY = b"""name: Legacy Quality
on:
  pull_request:
permissions:
  contents: read
jobs:
  quality-checks:
    runs-on: ubuntu-latest
    steps:
      - run: echo legacy-quality-fixture
"""
LEGACY_QUALITY = ".github/workflows/legacy-quality.yml"
TEMPORARY_QUALITY = ".github/workflows/merge-preparation-legacy-quality.yml"
MAPPING = LEGACY_QUALITY + "=" + TEMPORARY_QUALITY
PUBLISHER = ".github/workflows/release.yml"


class BootstrapMigrationTest(unittest.TestCase):
    fixture = preparation_tests.MergePreparationTest.fixture
    controller_fixture = preparation_tests.MergePreparationTest.controller_fixture

    @classmethod
    def setUpClass(cls):
        preparation_tests.MergePreparationTest.setUpClass.__func__(cls)
        result = subprocess.run(["copier", "copy", "--trust", "--defaults", "--overwrite",
                                 "-d", "use_python=true", "-d", "use_gh_actions_release=true",
                                 "-d", "project_version=1.2.0", str(ROOT), str(cls.project)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.policy = json.loads((cls.project / ".github/merge-preparation.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def legacy_fixture(self):
        git, gh, fetch, save, generated, _ = self.controller_fixture()
        changes = {path: None for path in git.entries(generated) if path.startswith(".github/")}
        changes.update({LEGACY_QUALITY: QUALITY, "app.txt": b"legacy product\n",
                        ".github/workflows/old-publisher.yml": b"name: old publisher\non: [push]\n",
                        ".github/workflows/custom-scheduled-publisher.yml": b"name: custom publisher\non: [workflow_dispatch]\n"})
        legacy = git.commit(git.patch_tree(generated, changes), [])
        target = git.commit(git.patch_tree(generated, {"app.txt": b"updated product\n"}), [legacy])
        return git, gh, fetch, save, legacy, target

    def activate(self, git, gh, legacy, target):
        m = self.module
        tree, report = m.bootstrap_tree(git, legacy, target, [MAPPING], QUALITY)
        gh.base = git.commit(tree, [legacy])
        gh.head = git.commit(git.text("rev-parse", target + "^{tree}"), [gh.base])
        gh.body = '<!-- release-classification ' + json.dumps({
            "head": gh.head, "diff": m.input_diff(git, gh.base, gh.head, m.load_policy(git, gh.base)),
            "level": "patch", "reason": "Reviewed initial migration"}) + ' -->'
        return report

    def test_missing_bootstrap_and_partial_installation_have_distinct_diagnostics(self):
        m = self.module
        git, _, _, _, legacy, target = self.legacy_fixture()
        with self.assertRaisesRegex(m.PreparationError, "Initial control installation is missing"):
            m.load_policy(git, legacy)
        partial = git.commit(git.patch_tree(legacy, {m.SCRIPT: git.blob(target, m.SCRIPT)}), [legacy])
        with self.assertRaisesRegex(m.PreparationError, "repair the partial installation"):
            m.bootstrap_tree(git, partial, target)
        # Bootstrap inspection never asks for an App key or setup permission.
        with mock.patch.dict(os.environ, {}, clear=True):
            tree, _ = m.bootstrap_tree(git, legacy, target)
        self.assertIn(m.SCRIPT, git.entries(tree))

    def test_seed_preserves_product_and_version_and_suppresses_all_publishers(self):
        m = self.module
        git, _, _, _, legacy, target = self.legacy_fixture()
        tree, report = m.bootstrap_tree(git, legacy, target, [MAPPING], QUALITY)
        self.assertEqual(m.bootstrap_tree(git, legacy, target, [MAPPING], QUALITY), (tree, report))
        seed = git.commit(tree, [legacy])
        self.assertEqual(m.bootstrap_check(git, legacy, seed, target, [MAPPING], QUALITY), report)
        self.assertEqual(m.read_version(git, seed, self.policy), "1.2.0")
        self.assertEqual(git.blob(seed, "app.txt"), b"legacy product\n")
        self.assertEqual(git.blob(seed, ".copier-answers.yml"), git.blob(legacy, ".copier-answers.yml"))
        self.assertIsNone(git.blob(seed, m.PLAN, optional=True))
        self.assertIsNone(git.blob(seed, PUBLISHER, optional=True))
        self.assertIsNone(git.blob(seed, ".github/workflows/old-publisher.yml", optional=True))
        self.assertIsNone(git.blob(seed, ".github/workflows/custom-scheduled-publisher.yml", optional=True))
        self.assertEqual(git.blob(seed, TEMPORARY_QUALITY), QUALITY)
        policy = m.load_policy(git, seed)
        self.assertNotIn(TEMPORARY_QUALITY, {w["path"] for w in policy["ci"]})
        for workflow in policy["ci"]:
            self.assertIn(b"workflow_dispatch", git.blob(seed, workflow["path"]))
        activation = m.candidate_ci_contract(git, seed, target, policy)
        self.assertIsNone(activation[TEMPORARY_QUALITY])
        self.assertIsNone(activation[".github/workflows/merge-preparation-bootstrap.yml"])
        self.assertEqual(m.digest(activation), report["activation_ci_digest"])
        self.assertEqual(policy["approved_ci_changes"], [report["activation_ci_digest"]])
        with self.assertRaisesRegex(m.PreparationError, "Missing regular data file"):
            m.restore(git, seed, self.keyring, 1)

    def test_seed_verification_rejects_product_changes_publishers_and_incomplete_control(self):
        m = self.module
        git, _, _, _, legacy, target = self.legacy_fixture()
        tree, _ = m.bootstrap_tree(git, legacy, target)
        changes = [{"app.txt": b"premature product update\n"},
                   {PUBLISHER: git.blob(target, PUBLISHER)}, {m.SCRIPT: None},
                   {m.PLAN: b"{}\n"}, {m.POLICY: m.canonical(self.policy)}]
        for delta in changes:
            with self.subTest(paths=list(delta)), self.assertRaisesRegex(m.PreparationError, "differs from the reviewed bootstrap tree"):
                m.bootstrap_check(git, legacy, git.patch_tree(tree, delta), target)
        # Candidate code is compared as bytes, never imported or executed.
        altered = git.patch_tree(tree, {m.SCRIPT: b"raise RuntimeError('candidate executed')\n"})
        with self.assertRaises(m.PreparationError):
            m.bootstrap_check(git, legacy, altered, target)

    def test_legacy_quality_cannot_reintroduce_credentials_or_collision_checks(self):
        m = self.module
        git, _, _, _, legacy, target = self.legacy_fixture()
        for forbidden in (b"environment: production", b"${{ secrets.PUBLISH_KEY }}", b"contents: write", b"Version Tag Check"):
            bad_base = git.commit(git.patch_tree(legacy, {LEGACY_QUALITY: QUALITY + forbidden + b"\n"}), [legacy])
            with self.subTest(forbidden=forbidden), self.assertRaisesRegex(m.PreparationError, "Bootstrap quality must"):
                m.bootstrap_tree(git, bad_base, target, [MAPPING])
        for blob in (QUALITY.replace(b"permissions:\n  contents: read\n", b""), QUALITY.replace(b"contents: read", b"contents: WRITE")):
            bad_base = git.commit(git.patch_tree(legacy, {LEGACY_QUALITY: blob}), [legacy])
            with self.assertRaisesRegex(m.PreparationError, "Bootstrap quality must"):
                m.bootstrap_tree(git, bad_base, target, [MAPPING])
        with self.assertRaisesRegex(m.PreparationError, "collides"):
            m.bootstrap_tree(git, legacy, target, [MAPPING, MAPPING])

    def test_resume_requires_exact_preapproved_contract(self):
        m = self.module
        git, gh, _, _, legacy, target = self.legacy_fixture()
        self.activate(git, gh, legacy, target)
        policy = m.load_policy(git, gh.base)
        m.approve_ci_changes(git, gh.base, gh.head, policy)
        for delta in ({PUBLISHER: git.blob(target, PUBLISHER) + b"# unexpected change\n"},
                      {TEMPORARY_QUALITY: QUALITY},
                      {".github/workflows/unreviewed.yml": QUALITY}):
            with self.subTest(paths=list(delta)), self.assertRaisesRegex(m.PreparationError, "without prior base approval"):
                m.approve_ci_changes(git, gh.base, git.patch_tree(gh.head, delta), policy)
        policy = {**policy, "approved_ci_changes": []}
        unapproved = git.patch_tree(gh.base, {m.POLICY: m.canonical(policy)})
        with self.assertRaisesRegex(m.PreparationError, "without prior base approval"):
            m.approve_ci_changes(git, unapproved, gh.head, policy)

    def test_seed_cannot_copy_dependencies_or_revision_with_trusted_control(self):
        m = self.module
        git, _, _, _, legacy, target = self.legacy_fixture()
        policy = {**self.policy, "trusted_paths": [*self.policy["trusted_paths"], "pyproject.toml"]}
        altered = git.patch_tree(target, {m.POLICY: m.canonical(policy),
                                         "pyproject.toml": git.blob(target, "pyproject.toml") + b"\n[tool.unexpected]\nchanged = true\n"})
        with self.assertRaisesRegex(m.PreparationError, "must preserve version-source/lock/revision"):
            m.bootstrap_tree(git, legacy, altered)

    def test_executable_trusted_helpers_keep_their_reviewed_modes(self):
        m = self.module
        git, _, _, _, legacy, target = self.legacy_fixture()
        path = "scripts/quality-helper.sh"
        target = git.patch_tree(target, {path: b"#!/bin/sh\nexit 0\n"}, {path: "100755"})
        tree, _ = m.bootstrap_tree(git, legacy, target)
        self.assertEqual(git.entries(tree)[path], git.entries(target)[path])
        self.assertEqual(git.entries(tree)[path][0], "100755")

    def test_required_check_handoff_is_after_control_installation(self):
        m = self.module
        git, _, _, _, legacy, target = self.legacy_fixture()
        rules = [{"type": "pull_request"}, {"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": True,
            "required_status_checks": [{"context": "quality-checks", "integration_id": 15368}]}}]
        gh = mock.Mock()
        gh.repo.side_effect = lambda path, **_: rules if path == "/rules/branches/main" else None
        with self.assertRaisesRegex(m.PreparationError, "Configure required"):
            m.preflight_protection(gh, {"default_branch": "main"}, 69)
        # Admission is possible with legacy protection and without App setup.
        tree, _ = m.bootstrap_tree(git, legacy, target, [MAPPING], QUALITY)
        m.bootstrap_check(git, legacy, tree, target, [MAPPING], QUALITY)
        m.load_policy(git, tree)
        checks = rules[1]["parameters"]["required_status_checks"]
        checks.append({"context": m.CHECK, "integration_id": 69})
        with self.assertRaisesRegex(m.PreparationError, "Migrate every existing"):
            m.preflight_protection(gh, {"default_branch": "main"}, 69)
        checks.pop(0)
        m.preflight_protection(gh, {"default_branch": "main"}, 69)
        checks[0]["integration_id"] = 15368
        with self.assertRaisesRegex(m.PreparationError, "Configure required"):
            m.preflight_protection(gh, {"default_branch": "main"}, 69)

    def test_activation_failures_retry_without_unsigned_release_or_duplicate_numbering(self):
        m = self.module
        git, gh, fetch, save, legacy, target = self.legacy_fixture()
        self.activate(git, gh, legacy, target)
        h0 = gh.head
        repo, app = {"id": 1, "default_branch": "main"}, {"id": 69, "bot_id": 70}
        with (mock.patch.object(git, "fetch", side_effect=fetch),
              mock.patch.object(m, "save_remote_commit", side_effect=save) as commits,
              mock.patch.object(m, "remote_collisions", return_value=[]) as collisions,
              mock.patch.object(m, "evaluate_ci", return_value=False) as ci,
              mock.patch.dict(os.environ, {"GITHUB_SHA": gh.base})):
            collisions.side_effect = m.PreparationError("registry lookup failed")
            m.reconcile_pr(gh, git, 11, repo, app, self.private, self.keyring)
            self.assertEqual(gh.checks[-1]["state"], "action_required")
            self.assertFalse(commits.called)
            self.assertFalse(ci.called)
            collisions.side_effect = None
            commits.side_effect = OSError("commit transport failed before update")
            m.reconcile_pr(gh, git, 11, repo, app, self.private, self.keyring)
            self.assertEqual(gh.head, h0)
            commits.side_effect = save
            m.reconcile_pr(gh, git, 11, repo, app, self.private, self.keyring)
            self.assertEqual(gh.checks[-1]["state"], "pending")
            h1 = gh.head
            self.assertNotEqual(h1, h0)
            # Waiting and retries use exactly the same signed record/version.
            for complete in (False, True, True):
                ci.return_value = complete
                m.reconcile_pr(gh, git, 11, repo, app, self.private, self.keyring)
                self.assertEqual(gh.head, h1)
            self.assertEqual(gh.checks[-1]["state"], "success")
            self.assertEqual(len(gh.commits), 1)
            self.assertEqual(ci.call_args.args[2]["base"], gh.base)
            self.assertEqual(ci.call_args.args[2]["head"], h1)
        for parents in ([gh.base], [h0]):
            published = git.commit(git.text("rev-parse", h1 + "^{tree}"), parents)
            restored = m.restore(git, published, self.keyring, 1)
            m.validate_publisher(restored, {"RELEASE_PUBLISHER_KIND": "github"})
            self.assertEqual(restored["generated_version"], "1.2.1")
            self.assertEqual(restored["executor_sha"], gh.base)
            self.assertEqual(m.restore(git, published, self.keyring, 1), restored)
            self.assertIsNone(git.blob(published, TEMPORARY_QUALITY, optional=True))
            self.assertIsNone(git.blob(published, ".github/workflows/merge-preparation-bootstrap.yml", optional=True))
            altered = git.patch_tree(published, {PUBLISHER: git.blob(published, PUBLISHER) + b"# changed\n"})
            with self.assertRaisesRegex(m.PreparationError, "Published tree differs"):
                m.restore(git, altered, self.keyring, 1)

    def test_bootstrap_cli_is_inspection_without_credentials_or_commits(self):
        m = self.module
        git, _, _, _, legacy, target = self.legacy_fixture()
        tree, _ = m.bootstrap_tree(git, legacy, target)
        seed = git.commit(tree, [legacy])
        before = git.text("rev-parse", "HEAD")
        for action, extra in (("bootstrap-tree", []), ("bootstrap-check", ["--head", seed])):
            argv = ["preparation", action, "--root", git.root, "--base", legacy, "--candidate", target, *extra]
            with (mock.patch.object(sys, "argv", argv), mock.patch.object(m, "GitHub") as github,
                  mock.patch.object(m, "sign") as signing, mock.patch.dict(os.environ, {}, clear=True),
                  mock.patch.object(m, "choose") as numbering,
                  mock.patch("builtins.print") as printed):
                m.main()
                self.assertFalse(github.called)
                self.assertFalse(signing.called)
                self.assertFalse(numbering.called)
                self.assertEqual(json.loads(printed.call_args.args[0])["tree"], tree)
            self.assertEqual(git.text("rev-parse", "HEAD"), before)

    def test_first_signed_release_recovers_after_publication_response_loss(self):
        m = self.module
        git, gh, fetch, save, legacy, target = self.legacy_fixture()
        self.activate(git, gh, legacy, target)
        with (mock.patch.object(git, "fetch", side_effect=fetch),
              mock.patch.object(m, "save_remote_commit", side_effect=save),
              mock.patch.object(m, "remote_collisions", return_value=[]),
              mock.patch.object(m, "evaluate_ci", return_value=True),
              mock.patch.dict(os.environ, {"GITHUB_SHA": gh.base})):
            m.reconcile_pr(gh, git, 11, {"id": 1, "default_branch": "main"}, {"id": 69, "bot_id": 70}, self.private, self.keyring)
        published = git.commit(git.text("rev-parse", gh.head + "^{tree}"), [gh.base])
        git.command("update-ref", "HEAD", published)
        folder = Path(git.root) / ".fixture-remote"
        folder.mkdir()
        origin = folder / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
        git.command("remote", "add", "origin", str(origin))
        git.command("config", "user.name", "Fixture")
        git.command("config", "user.email", "fixture@example.invalid")
        fake_bin = folder / "mock-bin"
        fake_bin.mkdir()
        remote = folder / "github-release-state.json"
        remote.write_text(json.dumps({"created": False, "fail_response": True, "creates": 0}))
        fake = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ["FIXTURE_RELEASE_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
if Path(sys.argv[0]).name == "curl":
    Path(args[args.index("--output") + 1]).write_text("{}")
    print("200" if state["created"] else "404", end="")
elif args[:2] == ["release", "create"]:
    assert not state["created"], "Existing release must not be overwritten"
    state["created"] = True
    state["creates"] += 1
    failed = state.pop("fail_response", False)
    path.write_text(json.dumps(state))
    sys.exit(1 if failed else 0)
else:
    raise AssertionError(args)
'''
        for name in ("gh", "curl"):
            executable = fake_bin / name
            executable.write_text(fake)
            executable.chmod(0o755)
        state_script = test_template.TemplateTest.workflow_step_script(self.project, "release.yml", "Inspect release state")
        tag_script = test_template.TemplateTest.workflow_step_script(self.project, "release.yml", "Create version tag")
        release_script = test_template.TemplateTest.workflow_step_script(self.project, "release.yml", "Create GitHub Release")
        release_script = release_script.replace("${{ github.server_url }}/${{ github.repository }}/tree/", "https://github.example/owner/project/tree/")
        output = folder / "release-output.txt"
        env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
               "FIXTURE_RELEASE_STATE": str(remote), "GITHUB_OUTPUT": str(output),
               "GITHUB_API_URL": "https://github.example/api", "GITHUB_REPOSITORY": "owner/project",
               "GH_TOKEN": "fixture-read-token", "TAG": "1.2.1", "PREPARED_RELEASE_TAG": "1.2.1"}
        run_step = lambda script: subprocess.run(["bash"], cwd=git.root, env=env, input=script, capture_output=True, text=True)
        for attempt in range(2):
            # Exactly the same signed commit is restored before each attempt.
            payload = m.restore(git, published, self.keyring, 1)
            m.validate_publisher(payload, {"RELEASE_PUBLISHER_KIND": "github"})
            self.assertEqual(payload["publication"]["release_tag"], env["TAG"])
            output.write_text("")
            state = run_step(state_script)
            self.assertEqual(state.returncode, 0, state.stderr)
            flags = dict(line.split("=", 1) for line in output.read_text().splitlines())
            if flags["tag_exists"] == "false":
                tagged = run_step(tag_script)
                self.assertEqual(tagged.returncode, 0, tagged.stderr)
            if flags["release_exists"] == "false":
                release = run_step(release_script)
                self.assertEqual(release.returncode, 1)  # Remote creation succeeded; response was lost.
            if attempt:
                self.assertEqual(flags["tag_exists"], "true")
                self.assertEqual(flags["release_exists"], "true")
        self.assertEqual(json.loads(remote.read_text())["creates"], 1)
        self.assertEqual(git.text("rev-parse", "refs/tags/1.2.1^{commit}"), published)
        # The actual state reader rejects a tag owned by another commit.
        subprocess.run(["git", "--git-dir", str(origin), "update-ref", "refs/tags/1.2.1", gh.base], check=True)
        rejected = run_step(state_script)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("not", rejected.stdout)

    def test_all_publishers_and_bootstrap_example_have_verified_restart_paths(self):
        m = self.module
        cases = [("use_gh_actions_release=true",),
                 ("use_chrome_extension=true", "use_gh_actions_chrome_extension_release=true"),
                 ("use_tauri=true", "use_gh_actions_tauri_build=true"),
                 ("use_docker=true", "use_gh_actions_docker_release=true"),
                 ("use_docker=true", "use_gh_actions_docker_project_pipeline=true")]
        for answers in cases:
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as directory:
                project = Path(directory) / "project"
                copied = subprocess.run(["copier", "copy", "--trust", "--defaults", "-d", "use_python=false",
                                         *sum((["-d", a] for a in answers), []), str(ROOT), str(project)], capture_output=True, text=True)
                self.assertEqual(copied.returncode, 0, copied.stderr)
                if "use_gh_actions_docker_project_pipeline=true" in answers:
                    # This pipeline deliberately requires a reviewed
                    # project-owned hook; Copier does not invent one.
                    (project / ".github/scripts/docker-image-project.sh").write_text("#!/bin/sh\n# Reviewed fixture hook\nexit 0\n")
                policy = json.loads((project / m.POLICY).read_text())
                workflows = list((project / ".github/workflows").glob("*.yml"))
                example = project / "docs/examples/merge-preparation-bootstrap.yml"
                m.bootstrap_quality(example.read_bytes())
                lint = subprocess.run(["actionlint", "-ignore", '^unexpected key "queue" for "concurrency" section',
                                       *map(str, workflows), str(example)], capture_output=True, text=True)
                self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
                ci_paths = {project / w["path"] for w in policy["ci"]}
                publishers = [w for w in workflows if w not in ci_paths and "Restore signed publication plan" in w.read_text()]
                self.assertTrue(publishers)
                for workflow in publishers:
                    self.assertIn("  workflow_dispatch:", workflow.read_text())
                    script = test_template.TemplateTest.workflow_step_script(project, workflow.name, "Restore signed publication plan")
                    refused = subprocess.run(["bash"], input=script, cwd=project,
                                             env={**os.environ, "GITHUB_REF": "refs/heads/untrusted"}, capture_output=True, text=True)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn("Publication requires main", refused.stderr)
                # The same seed builder handles runtime-specific trusted JSON
                # and helpers; no special bootstrap option is rendered.
                subprocess.run(["git", "init", "-q", str(project)], check=True)
                case_git = m.Git(project)
                case_git.command("add", ".")
                final_tree = case_git.text("write-tree")
                old_changes = {p: None for p in case_git.entries(final_tree) if p.startswith(".github/")}
                for path in policy.get("trusted_json", {}):
                    old_data = json.loads(case_git.blob(final_tree, path))
                    old_data.update(description="Legacy product", dependencies={"legacy-dependency": "1.0.0"},
                                    scripts={"legacy": "echo legacy"})
                    old_changes[path] = m.canonical(old_data)
                old = case_git.commit(case_git.patch_tree(final_tree, old_changes), [])
                final = case_git.commit(final_tree, [old])
                seed_tree, _ = m.bootstrap_tree(case_git, old, final)
                for workflow in publishers:
                    self.assertIsNone(case_git.blob(seed_tree, str(workflow.relative_to(project)), optional=True))
                self.assertEqual(m.read_version(case_git, seed_tree, policy), m.read_version(case_git, old, policy))
                for path in policy.get("trusted_json", {}):
                    seeded = json.loads(case_git.blob(seed_tree, path))
                    self.assertEqual(seeded["dependencies"], {"legacy-dependency": "1.0.0"})
                    self.assertEqual(seeded["description"], "Legacy product")
                    self.assertEqual(seeded["scripts"], json.loads(case_git.blob(final, path))["scripts"])


if __name__ == "__main__":
    unittest.main()
