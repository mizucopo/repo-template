import copy
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class MergePreparationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.project = Path(cls.temporary.name) / "project"
        result = subprocess.run(["copier", "copy", "--trust", "--defaults", "-d", "use_python=false",
                                 "-d", "project_version=1.2.0", str(ROOT), str(cls.project)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        spec = importlib.util.spec_from_file_location("merge_preparation", cls.project / ".github/scripts/merge-preparation.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.policy = json.loads((cls.project / ".github/merge-preparation.json").read_text())
        key = Path(cls.temporary.name) / "key.pem"
        subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
        cls.private = key.read_text()
        cls.public = subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout"], check=True, capture_output=True, text=True).stdout
        cls.keyring = {cls.module.key_id(cls.public): cls.public}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        git = self.module.Git(root)
        for path in self.project.rglob("*"):
            if path.is_file():
                target = root / path.relative_to(self.project)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
        git.command("add", ".")
        tree = git.text("write-tree")
        base = git.commit(tree, [])
        git.command("update-ref", "HEAD", base)
        tree = git.patch_tree(base, {"feature.txt": b"feature\n"})
        head = git.commit(tree, [base])
        return root, git, base, head

    def signed(self, git, base, head, policy=None, version="1.2.1"):
        policy = policy or self.policy
        tree = self.module.update_versions(git, git.merge_tree(base, head, policy), policy, version, None)
        payload = {"repository_id": 1, "pull_request": 11, "input_head": head,
                   "input_diff": self.module.input_diff(git, base, head, policy), "input_version": "1.2.0",
                   "generated_base_sha": base, "executor_sha": base, "generated_version": version,
                   "generated_revision": None, "explicit_minimum": None, "explicit_revision": None,
                   "level": "patch", "policy": policy, "publication": self.module.publication(policy, version),
                   "ci_contract": self.module.ci_contract(git, base, policy), "merge_tree_digest": git.projection(tree)}
        record = self.module.sign(payload, self.private)
        tree = git.patch_tree(tree, {self.module.PLAN: self.module.canonical(record) + b"\n"})
        parents = [head] if git.text("merge-base", base, head) == base else [head, base]
        return git.commit(tree, parents), record

    def test_version_precedence_and_increment_width(self):
        m = self.module
        self.assertEqual(m.version_key("1.2.3", "chrome"), m.version_key("1.2.3.0", "chrome"))
        self.assertEqual(m.version_key("1.2.3+foo", "semver"), m.version_key("1.2.3+bar", "semver"))
        self.assertEqual(m.bump("0.9.9", "major", "semver"), "1.0.0")
        self.assertEqual(m.bump("1.2.3.4", "patch", "chrome"), "1.2.3.5")
        self.assertEqual(m.bump("1.2", "patch", "chrome"), "1.2.1")
        self.assertEqual(m.bump("1.2.3.4", "minor", "chrome"), "1.3.0.0")
        self.assertEqual(m.bump("1.2.3-alpha.9", "patch", "semver"), "1.2.3-alpha.10")
        versions = ["1.2.3-alpha.1", "1.2.3-alpha.2", "1.2.3-alpha.10", "1.2.3-beta.1", "1.2.3"]
        self.assertEqual(versions, sorted(versions, key=lambda v: m.version_key(v, "semver")))
        with self.assertRaisesRegex(m.PreparationError, "65535"):
            m.bump("1.2.65535", "patch", "chrome")

    def test_explicit_floor_is_separate_from_bot_generated_candidate(self):
        m = self.module
        choose = lambda base, previous=None, explicit=None: m.choose(self.policy, base, "patch", explicit, lambda _: [], previous=previous)[0]
        self.assertEqual(choose("1.2.0"), "1.2.1")
        self.assertEqual(choose("1.2.0", ("1.2.1", None)), "1.2.1")
        self.assertEqual(choose("1.2.1", ("1.2.1", None)), "1.2.2")
        self.assertEqual(choose("1.2.0", explicit="1.3.0"), "1.3.0")
        with self.assertRaisesRegex(m.PreparationError, "Explicit minimum"):
            choose("1.2.1", explicit="1.2.1")

    def test_build_metadata_and_descending_prerelease_are_not_updates(self):
        for base, explicit in [("1.2.3", "1.2.3+build"), ("1.2.3-beta.1", "1.2.3-alpha.2"),
                               ("1.2.3", "1.2.3-rc.1")]:
            with self.subTest(base=base), self.assertRaises(self.module.PreparationError):
                self.module.choose(self.policy, base, "patch", explicit, lambda _: [])

    def test_aliases_are_not_collision_candidates_and_stalled_search_stops(self):
        policy = copy.deepcopy(self.policy)
        policy["publication"]["images"] = [{"name": "image", "repository": "owner/image", "tag": "{version}", "aliases": ["latest"]}]
        observed = []
        def occupied(plan):
            observed.append(plan)
            return ["image"] if plan["images"][0]["tag"] == "1.2.1" else []
        version, _, _ = self.module.choose(policy, "1.2.0", "patch", None, occupied)
        self.assertEqual(version, "1.2.2")
        self.assertTrue(all(p["images"][0]["tag"] != "latest" for p in observed))
        policy["version"].update(scheme="upstream-revision", revision_path="revision")
        with self.assertRaisesRegex(self.module.PreparationError, "image cannot advance"):
            self.module.choose(policy, "1.2.0", "patch", None, lambda _: ["image"], base_revision="r0")
        with self.assertRaisesRegex(self.module.PreparationError, "100 candidates"):
            self.module.choose(self.policy, "1.2.0", "patch", None, lambda _: ["release_tag"])

    def test_upstream_revision_never_bumps_upstream_for_collisions(self):
        policy = copy.deepcopy(self.policy)
        policy["version"].update(scheme="upstream-revision", revision_path="revision")
        policy["publication"]["release_tag"] = "{version}{revision_suffix}"
        version, revision, _ = self.module.choose(policy, "1.2.0", "major", None,
            lambda p: ["release_tag"] if p["release_tag"] == "1.2.0-r1" else [], base_revision="r0")
        self.assertEqual((version, revision), ("1.2.0", "r2"))
        version, revision, _ = self.module.choose(policy, "1.2.0", "patch", None,
            lambda _: [], base_revision="r5", input_version="1.2.0+rebuild")
        self.assertEqual((version, revision), ("1.2.0+rebuild", "r6"))

    def test_api_log_redirect_does_not_forward_app_credential(self):
        m = self.module
        original = m.request.Request("https://api.github.com/logs", headers={"Authorization": "Bearer test-only"})
        handler = m.CredentialRedirect()
        external = handler.redirect_request(original, None, 302, "", {}, "https://logs.example.invalid/signed")
        self.assertIsNone(external.get_header("Authorization"))
        internal = handler.redirect_request(original, None, 302, "", {}, "https://api.github.com/new")
        self.assertEqual(internal.get_header("Authorization"), "Bearer test-only")
        with self.assertRaisesRegex(m.PreparationError, "non-HTTPS"):
            handler.redirect_request(original, None, 302, "", {}, "http://logs.example.invalid/plain")

    def test_dependabot_grouped_updates_account_for_complete_delta(self):
        m = self.module
        _, git, initial, _ = self.fixture()
        initial = git.commit(git.patch_tree(initial, {"dependencies.json": b'{"dependencies":{"foo":"^1.2.0","unknown":"2.0.0"}}'}), [initial])
        rule = {"ecosystem": "npm", "usage": "development", "compatible_minor_patch": True,
                "source": {"path": "dependencies.json", "format": "json", "key": ["dependencies", "foo"]}}
        policy = {"paths": ["dependencies.json"], "dependencies": {"foo": rule}}
        gh = mock.Mock()
        gh.repo.return_value = {"author": {"login": "dependabot[bot]"}, "commit": {"verification": {"verified": True}}}
        good = git.commit(git.patch_tree(initial, {"dependencies.json": b'{"dependencies":{"foo":"^1.3.0","unknown":"2.0.0"}}'}), [initial])
        self.assertEqual(m.dependency_evidence(gh, git, initial, good, policy)[0]["name"], "foo")
        mixed = git.commit(git.patch_tree(initial, {"dependencies.json": b'{"dependencies":{"foo":"^1.3.0","unknown":"3.0.0"}}'}), [initial])
        with self.assertRaisesRegex(m.PreparationError, "undeclared"):
            m.dependency_evidence(gh, git, initial, mixed, policy)
        gh.repo.return_value["commit"]["verification"]["verified"] = False
        with self.assertRaisesRegex(m.PreparationError, "unverified"):
            m.dependency_evidence(gh, git, initial, good, policy)

    def test_toml_and_json_lock_updates_are_data_only(self):
        m = self.module
        blob = b'[project]\nname = "test"\nversion = "1.2.0"\ndependencies = ["foo"]\n\n[tool.test]\nversion = "do-not-touch"\n'
        spec = {"path": "pyproject.toml", "format": "toml", "key": ["project", "version"]}
        changed = m.write_field(blob, spec, "1.2.1")
        self.assertIn(b'version = "do-not-touch"', changed)
        self.assertIn(b'dependencies = ["foo"]', changed)
        lock = b'[[package]]\nname = "test"\nversion = "1.2.0"\n\n[[package]]\nname = "dependency"\nversion = "3.0.0"\n'
        changed = m.write_field(lock, {"path": "uv.lock", "format": "toml-lock", "package": "test"}, "1.2.1")
        self.assertIn(b'version = "3.0.0"', changed)
        _, git, base, _ = self.fixture()
        tree = git.patch_tree(base, {"package.json": b'{"version":"1.2.0"}', "package-lock.json": b'{"version":"1.2.0","packages":{"":{"version":"1.2.0"}}}'})
        policy = copy.deepcopy(self.policy)
        policy["version"]["sources"] = [{"path": "package.json", "format": "json", "key": ["version"]}]
        policy["version"]["locks"] = [{"path": "package-lock.json", "format": "json", "key": key} for key in (["version"], ["packages", "", "version"])]
        tree = m.update_versions(git, tree, policy, "1.2.1", None)
        data = json.loads(git.blob(tree, "package-lock.json"))
        self.assertEqual(data["version"], data["packages"][""]["version"])

    def test_h0_h1_merge_tree_signature_survives_squash_and_rebase(self):
        _, git, base, h0 = self.fixture()
        h1, record = self.signed(git, base, h0)
        self.assertEqual(record["payload"]["input_head"], h0)
        self.assertNotIn(h1, self.module.canonical(record).decode())
        merge = git.commit(git.merge_tree(base, h1), [base, h1])
        self.assertEqual(git.projection(merge), record["payload"]["merge_tree_digest"])
        for parents in ([base], [h0]):
            published = git.commit(git.text("rev-parse", h1 + "^{tree}"), parents)
            restored = self.module.restore(git, published, self.keyring, 1)
            self.assertEqual(restored, record["payload"])
        self.assertEqual(self.module.sign(record["payload"], self.private), record)

    def test_records_cannot_be_forged_missing_or_used_for_another_tree(self):
        _, git, base, h0 = self.fixture()
        h1, record = self.signed(git, base, h0)
        for bad in [None, {**record, "key_id": "unknown"}, {**record, "payload": {**record["payload"], "generated_version": "9.9.9"}}]:
            with self.subTest(record=bad is None), self.assertRaises((self.module.PreparationError, TypeError)):
                self.module.verify(bad, self.keyring)
        changed = git.commit(git.patch_tree(h1, {"feature.txt": b"altered\n"}), [h1])
        with self.assertRaisesRegex(self.module.PreparationError, "Published tree differs"):
            self.module.restore(git, changed, self.keyring, 1)
        with self.assertRaisesRegex(self.module.PreparationError, "Missing regular data file"):
            self.module.restore(git, base, self.keyring, 1)
        with self.assertRaisesRegex(self.module.PreparationError, "another repository"):
            self.module.restore(git, h1, self.keyring, 2)

    def test_frozen_publisher_destination_and_alias_contract_are_checked(self):
        policy = copy.deepcopy(self.policy)
        policy["publication"].update(github_release=True, latest_image="extended")
        policy["publication"]["images"] = [{"name": "extended", "repository": "owner/image", "registry": "dockerhub", "tag": "{version}", "aliases": ["latest"]}]
        payload = {"generated_version": "1.2.1", "publication": self.module.publication(policy, "1.2.1")}
        env = {"RELEASE_PUBLISHER_KIND": "docker", "RELEASE_EXPECTED_IMAGE_REPOSITORY": "owner/image", "RELEASE_EXPECTED_IMAGE_REGISTRY": "dockerhub"}
        self.module.validate_publisher(payload, env)
        for change, message in [({"RELEASE_EXPECTED_IMAGE_REPOSITORY": "other/image"}, "destination"),
                                ({"RELEASE_PUBLISHER_KIND": "github"}, "image/tag")]:
            with self.assertRaisesRegex(self.module.PreparationError, message):
                self.module.validate_publisher(payload, {**env, **change})
        changed = copy.deepcopy(payload)
        changed["publication"]["images"][0]["aliases"] = []
        with self.assertRaisesRegex(self.module.PreparationError, "tag/alias"):
            self.module.validate_publisher(changed, env)

    def test_missing_app_configuration_reports_separate_setup_before_commit(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MERGE_PREPARATION_")}
        command = ["python3", "-I", str(self.project / ".github/scripts/merge-preparation.py"), "configuration-check"]
        missing = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Missing MERGE_PREPARATION_APP_PRIVATE_KEY", missing.stderr)
        self.assertIn("separately approve", missing.stderr)
        env.update(MERGE_PREPARATION_APP_PRIVATE_KEY=self.private, MERGE_PREPARATION_PUBLIC_KEYS=json.dumps(self.keyring),
                   MERGE_PREPARATION_APP_CLIENT_ID="Iv.test", MERGE_PREPARATION_APP_ID="69", MERGE_PREPARATION_APP_BOT_ID="70")
        valid = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        env["MERGE_PREPARATION_PUBLIC_KEYS"] = "{}"
        mismatched = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(mismatched.returncode, 0)
        self.assertIn("before preparing any commit", mismatched.stderr)
        self.assertNotIn(self.private, missing.stderr + valid.stderr + mismatched.stderr)

    def test_managed_plan_conflicts_reconcile_but_code_conflicts_stop(self):
        _, git, base, head = self.fixture()
        h1, _ = self.signed(git, base, head)
        other = git.patch_tree(base, {"version": b"1.2.1\n", "other.txt": b"other feature\n", self.module.PLAN: b"other signed plan\n"})
        new_base = git.commit(other, [base])
        merged = git.merge_tree(new_base, h1, self.policy)
        self.assertIsNone(git.blob(merged, self.module.PLAN, optional=True))
        self.assertEqual(git.blob(merged, "other.txt"), b"other feature\n")
        divergent = git.commit(git.patch_tree(base, {"feature.txt": b"different\n"}), [base])
        with self.assertRaises(self.module.PreparationError):
            git.merge_tree(divergent, h1, self.policy)

    def test_pr_workflow_script_and_local_action_cannot_replace_base_contract(self):
        _, git, base, head = self.fixture()
        policy = copy.deepcopy(self.policy)
        paths = [".github/workflows/ci.yml", ".github/scripts/verify.py", ".github/actions/verify/action.yml"]
        tree = git.patch_tree(base, {p: b"approved verification\n" for p in paths})
        base = git.commit(tree, [base])
        policy["ci"] = [{"path": paths[0], "name": "CI", "event": "workflow_dispatch", "jobs": ["quality"], "trusted_paths": paths[1:]}]
        for path in paths:
            candidate = git.commit(git.patch_tree(base, {path: b"return success; fake checkout SHA\n"}), [base])
            with self.subTest(path=path), self.assertRaisesRegex(self.module.PreparationError, "prior base approval"):
                self.module.approve_ci_changes(git, base, candidate, policy)
            # Candidate's attempt to approve itself is ignored.
            candidate = git.commit(git.patch_tree(candidate, {self.module.POLICY: b'{"ci":[],"approved_ci_changes":["anything"]}'}), [candidate])
            with self.assertRaises(self.module.PreparationError):
                self.module.approve_ci_changes(git, base, candidate, policy)
        malicious = git.patch_tree(base, {paths[1]: b"always succeed"})
        candidates = self.module.ci_contract(git, malicious, policy)
        staged = copy.deepcopy(policy)
        staged["approved_ci_changes"] = [self.module.digest(candidates)]
        self.assertEqual(self.module.approve_ci_changes(git, base, malicious, staged), self.module.ci_contract(git, base, policy))
        # Newly registered paths also enter the exact approval digest.
        declaration = json.loads(git.blob(base, self.module.POLICY))
        declaration["trusted_paths"].append("tests/new-validator.py")
        candidate = git.patch_tree(base, {self.module.POLICY: json.dumps(declaration).encode(), "tests/new-validator.py": b"approved code"})
        exact = self.module.candidate_ci_contract(git, base, candidate, policy)
        self.assertIn("tests/new-validator.py", exact)
        staged["approved_ci_changes"] = [self.module.digest(exact)]
        self.module.approve_ci_changes(git, base, candidate, staged)
        changed = git.patch_tree(candidate, {"tests/new-validator.py": b"always succeed"})
        with self.assertRaisesRegex(self.module.PreparationError, "prior base approval"):
            self.module.approve_ci_changes(git, base, changed, staged)

    def test_self_dependency_is_rejected(self):
        _, git, base, _ = self.fixture()
        for path in (".github/workflows/merge-preparation.yml", ".github/workflows/merge-preparation-events.yml"):
            policy = copy.deepcopy(self.policy)
            policy["ci"] = [{"path": path, "event": "workflow_dispatch", "jobs": ["prepare"], "trusted_paths": []}]
            tree = git.patch_tree(base, {self.module.POLICY: json.dumps(policy).encode()})
            with self.assertRaisesRegex(self.module.PreparationError, "depend on itself"):
                self.module.load_policy(git, tree)

    def test_environment_blocks_pr_refs_tags_wildcards_and_unrestricted_secrets(self):
        env = {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
        rules = {"branch_policies": [{"name": "main", "type": "branch"}]}
        self.module.check_environment(env, rules, "main", "refs/heads/main")
        for ref in ("refs/pull/1/merge", "refs/heads/feature", "refs/tags/main"):
            with self.subTest(ref=ref), self.assertRaises(self.module.PreparationError):
                self.module.check_environment(env, rules, "main", ref)
        for bad in ([{"name": "*", "type": "branch"}], [{"name": "main", "type": "tag"}],
                    rules["branch_policies"] + [{"name": "refs/pull/*/merge", "type": "branch"}]):
            with self.subTest(rules=bad), self.assertRaises(self.module.PreparationError):
                self.module.check_environment(env, {"branch_policies": bad}, "main", "refs/heads/main")
        with self.assertRaises(self.module.PreparationError):
            self.module.check_environment({"deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False}}, rules, "main", "refs/heads/main")

    def run_fixture(self):
        workflow = {"path": ".github/workflows/ci.yml", "event": "workflow_dispatch", "jobs": ["quality"]}
        expected = {"run_id": 7, "workflow_id": 9, "actor_id": 3, "actions_app_id": 15368,
                    "default_branch": "main", "attempt": 2, "head": "h1", "base": "b", "merge": "m", "contract": "approved"}
        run = {"id": 7, "workflow_id": 9, "actor": {"id": 3}, "event": "workflow_dispatch", "path": workflow["path"],
               "head_sha": "b", "head_branch": "main", "run_attempt": 2, "status": "completed"}
        jobs = [{"name": name, "run_attempt": 2, "runner_name": "GitHub Actions 1", "app": {"id": 15368, "slug": "github-actions"},
                 "conclusion": "success", "steps": [{"name": "Verify candidate checkout before PR execution", "conclusion": "success"}]} for name in ("control", "quality")]
        proof = {k: expected[k] for k in ("head", "base", "merge", "contract")}
        return run, jobs, workflow, expected, proof, {"quality": "m"}

    def test_ci_requires_exact_base_definition_head_merge_attempt_and_checkout(self):
        args = self.run_fixture()
        self.assertTrue(self.module.accept_run(*args))
        for key, value in [("head_sha", "old-base"), ("event", "pull_request"), ("run_attempt", 1),
                           ("actor", {"id": 999}), ("path", "other.yml")]:
            changed = copy.deepcopy(args)
            changed[0][key] = value
            with self.subTest(key=key), self.assertRaises(self.module.PreparationError):
                self.module.accept_run(*changed)
        for i, change in [(4, {**args[4], "merge": "fake"}), (5, {"quality": "fake"})]:
            changed = list(copy.deepcopy(args))
            changed[i] = change
            with self.assertRaises(self.module.PreparationError):
                self.module.accept_run(*changed)
        for result in ("failure", "skipped", "cancelled"):
            changed = copy.deepcopy(args)
            changed[1][1]["conclusion"] = result
            with self.assertRaises(self.module.PreparationError):
                self.module.accept_run(*changed)

    def test_same_head_classification_generations_and_stale_evidence(self):
        m = self.module
        pr = {"head": {"sha": "h1"}, "labels": [{"name": "release:patch"}],
              "body": '<!-- release-classification {"head":"h0","diff":"diff","level":"patch","reason":"fix"} -->'}
        events = [{"id": 1, "event": "labeled", "label": {"name": "release:patch"}, "actor": {"login": "maintainer"}}]
        _, gen1 = m.classification(pr, events, lambda _: True, "h0", "diff", "maintainer")
        pr["body"] = pr["body"].replace('"fix"', '"corrected reason"')
        _, gen2 = m.classification(pr, events, lambda _: True, "h0", "diff", "maintainer")
        self.assertNotEqual(gen1, gen2)
        events += [{"id": 2, "event": "unlabeled", "label": {"name": "release:patch"}, "actor": {"login": "maintainer"}},
                   {"id": 3, "event": "labeled", "label": {"name": "release:patch"}, "actor": {"login": "maintainer"}}]
        _, gen3 = m.classification(pr, events, lambda _: True, "h0", "diff", "maintainer")
        self.assertNotEqual(gen2, gen3)
        for changed in [{**pr, "labels": []}, {**pr, "labels": [{"name": "release:patch"}, {"name": "release:major"}]},
                        {**pr, "body": pr["body"].replace('"diff"', '"old"')}]:
            with self.assertRaises(m.PreparationError):
                m.classification(changed, events, lambda _: True, "h0", "diff", "maintainer")
        with self.assertRaises(m.PreparationError):
            m.classification(pr, events, lambda _: False, "h0", "diff", "reader")

    def test_dependabot_policy_requires_every_dependency_to_be_compatible(self):
        metadata = [{"name": "foo", "ecosystem": "pip", "usage": "development", "old_version": "1.2.0", "update": "minor"}]
        policy = {"paths": ["uv.lock"], "dependencies": {"foo": {"ecosystem": "pip", "usage": "development", "compatible_minor_patch": True}}}
        self.assertEqual(self.module.classify_dependencies(metadata, ["uv.lock"], policy), "patch")
        for items, paths in [([], ["uv.lock"]), (metadata, ["src/main.py"]), ([{**metadata[0], "update": "major"}], ["uv.lock"]),
                             ([{**metadata[0], "old_version": "0.2.0"}], ["uv.lock"]), (metadata + [{**metadata[0], "name": "unknown"}], ["uv.lock"])]:
            with self.assertRaises(self.module.PreparationError):
                self.module.classify_dependencies(items, paths, policy)

    def controller_fixture(self):
        _, git, base, h0 = self.fixture()
        m = self.module
        owner = self
        diff = m.input_diff(git, base, h0, self.policy)
        class FakeGitHub:
            repository = "owner/project"
            def __init__(self):
                self.base = base
                self.head = h0
                self.events = [{"id": 1, "event": "labeled", "label": {"name": "release:patch"}, "actor": {"login": "maintainer", "id": 8}}]
                self.body = '<!-- release-classification ' + json.dumps({"head": h0, "diff": diff, "level": "patch", "reason": "compatible fix"}) + ' -->'
                self.labels = [{"name": "release:patch"}]
                self.updated_at = "2026-10-04T00:00:00Z"
                self.merge = None
                self.checks = []
                self.commits = []
            def repo(self, path):
                if path == "/pulls/11":
                    return {"state": "open", "head": {"sha": self.head, "repo": {"id": 1}, "ref": "feature"},
                            "base": {"sha": self.base, "ref": "main"}, "labels": copy.deepcopy(self.labels), "body": self.body,
                            "updated_at": self.updated_at, "merge_commit_sha": self.merge,
                            "user": {"login": "maintainer"}}
                if path == "/commits/main":
                    return {"sha": self.base}
                raise AssertionError(path)
            def api(self, path, **kwargs):
                owner.assertEqual(path, "/graphql")
                return {"data": {"repository": {"pullRequest": {"editor": {"login": "maintainer"}, "author": {"login": "maintainer"}}}}}
            def pages(self, path):
                owner.assertEqual(path, "/issues/11/events")
                return copy.deepcopy(self.events)
            def permission(self, login):
                return login == "maintainer"
            def check(self, head, state, generation, message, check_id=None, **kwargs):
                record = {"id": check_id or len(self.checks) + 1, "head_sha": head, "state": state,
                          "external_id": generation, "message": message}
                self.checks.append(record)
                return record
        gh = FakeGitHub()
        def fetch(ref):
            if ref == "refs/pull/11/merge":
                if gh.merge is None or git.text("show", "-s", "--format=%P", gh.merge).split() != [gh.base, gh.head]:
                    gh.merge = git.commit(git.merge_tree(gh.base, gh.head), [gh.base, gh.head])
                (Path(git.root) / ".git/FETCH_HEAD").write_text(gh.merge + "\n")
        def save(ignored_gh, ignored_git, tree, parents, branch, expected_head):
            owner.assertEqual(gh.head, expected_head)
            gh.head = git.commit(tree, parents)
            gh.merge = None
            gh.commits.append(gh.head)
            return gh.head
        return git, gh, fetch, save, base, h0

    def test_controller_bot_updates_are_idempotent_and_ci_targets_h1(self):
        m = self.module
        git, gh, fetch, save, base, h0 = self.controller_fixture()
        with (mock.patch.object(git, "fetch", side_effect=fetch),
              mock.patch.object(m, "save_remote_commit", side_effect=save),
              mock.patch.object(m, "remote_collisions", return_value=[]),
              mock.patch.object(m, "evaluate_ci", return_value=True) as ci,
              mock.patch.object(m, "sign", wraps=m.sign) as sign,
              mock.patch.dict(os.environ, {"GITHUB_SHA": base})):
            for _ in range(2):
                m.reconcile_pr(gh, git, 11, {"id": 1, "default_branch": "main"}, {"id": 69, "bot_id": 70}, self.private, self.keyring)
            self.assertEqual(sign.call_count, 1)
            self.assertEqual(len(gh.commits), 1)
            self.assertNotEqual(gh.head, h0)
            self.assertEqual(ci.call_args.args[2]["head"], gh.head)
            self.assertEqual(gh.checks[-1]["state"], "success", gh.checks[-1]["message"])
            payload = m.restore(git, gh.head, self.keyring, 1)
            self.assertEqual(payload["input_head"], h0)
            self.assertEqual(git.projection(gh.merge), payload["merge_tree_digest"])

    def test_parallel_pr_reprepares_generated_version_without_false_explicit_floor(self):
        m = self.module
        git, gh, fetch, save, base, h0 = self.controller_fixture()
        with (mock.patch.object(git, "fetch", side_effect=fetch), mock.patch.object(m, "save_remote_commit", side_effect=save),
              mock.patch.object(m, "remote_collisions", return_value=[]), mock.patch.object(m, "evaluate_ci", return_value=True),
              mock.patch.dict(os.environ, {"GITHUB_SHA": base})):
            m.reconcile_pr(gh, git, 11, {"id": 1, "default_branch": "main"}, {"id": 69, "bot_id": 70}, self.private, self.keyring)
            self.assertEqual(m.restore(git, gh.head, self.keyring, 1)["generated_version"], "1.2.1")
            new_control = git.blob(base, m.SCRIPT) + b"\n# New approved base verification contract\n"
            gh.base = git.commit(git.patch_tree(base, {"version": b"1.2.1\n", "other.txt": b"merged PR A\n", m.SCRIPT: new_control}), [base])
            os.environ["GITHUB_SHA"] = gh.base
            m.reconcile_pr(gh, git, 11, {"id": 1, "default_branch": "main"}, {"id": 69, "bot_id": 70}, self.private, self.keyring)
            self.assertEqual(gh.checks[-1]["state"], "success", gh.checks[-1]["message"])
            payload = m.restore(git, gh.head, self.keyring, 1)
            self.assertEqual(payload["generated_version"], "1.2.2")
            self.assertIsNone(payload["explicit_minimum"])
            self.assertEqual(payload["input_head"], h0)
            self.assertEqual(git.blob(gh.head, "other.txt"), b"merged PR A\n")
            self.assertEqual(git.blob(gh.head, m.SCRIPT), new_control)
            self.assertEqual(payload["ci_contract"][m.SCRIPT], m.hashlib.sha256(new_control).hexdigest())

    def test_delayed_result_cannot_succeed_after_same_sha_label_change(self):
        m = self.module
        git, gh, fetch, save, base, _ = self.controller_fixture()
        def delayed(*_):
            gh.events.extend([{**gh.events[0], "id": 2, "event": "unlabeled"}, {**gh.events[0], "id": 3}])
            return True
        with (mock.patch.object(git, "fetch", side_effect=fetch), mock.patch.object(m, "save_remote_commit", side_effect=save),
              mock.patch.object(m, "remote_collisions", return_value=[]), mock.patch.object(m, "evaluate_ci", side_effect=delayed),
              mock.patch.dict(os.environ, {"GITHUB_SHA": base})):
            m.reconcile_pr(gh, git, 11, {"id": 1, "default_branch": "main"}, {"id": 69, "bot_id": 70}, self.private, self.keyring)
            self.assertEqual(gh.checks[-1]["state"], "action_required")
            self.assertIn("Classification generation changed", gh.checks[-1]["message"])
            self.assertFalse(any(c["state"] == "success" for c in gh.checks))

    def test_same_sha_reason_edit_and_revert_invalidates_delayed_success(self):
        m = self.module
        git, gh, fetch, save, base, _ = self.controller_fixture()
        def delayed(*_):
            gh.updated_at = "2026-10-04T00:01:00Z"
            return True
        with (mock.patch.object(git, "fetch", side_effect=fetch), mock.patch.object(m, "save_remote_commit", side_effect=save),
              mock.patch.object(m, "remote_collisions", return_value=[]), mock.patch.object(m, "evaluate_ci", side_effect=delayed),
              mock.patch.dict(os.environ, {"GITHUB_SHA": base})):
            m.reconcile_pr(gh, git, 11, {"id": 1, "default_branch": "main"}, {"id": 69, "bot_id": 70}, self.private, self.keyring)
            self.assertEqual(gh.checks[-1]["state"], "action_required")
            self.assertIn("Inputs changed", gh.checks[-1]["message"])
            self.assertFalse(any(c["state"] == "success" for c in gh.checks))

    def test_ci_dispatch_cache_does_not_increment_runs_and_ignores_old_attempt(self):
        m = self.module
        run, jobs, workflow, expected, proof, _ = self.run_fixture()
        expected.pop("run_id")
        expected.pop("workflow_id")
        expected.pop("attempt")
        expected.pop("contract")
        expected["app_id"] = 69
        cache = []
        class Fake:
            def __init__(self):
                self.dispatches = 0
            def repo(self, path, method="GET", payload=None, **kwargs):
                if path.endswith("/ci.yml"):
                    return {"id": 9}
                if path.endswith("/dispatches"):
                    self.dispatches += 1
                    return {"workflow_run_id": 7, "html_url": "https://github.com/owner/project/actions/runs/7"}
                if path == "/actions/runs/7":
                    return {**run, "html_url": "https://github.com/owner/project/actions/runs/7"}
                if path.startswith("/check-runs/"):
                    return {"app": {"id": 15368, "slug": "github-actions"}}
                raise AssertionError(path)
            def pages(self, path, key):
                if key == "check_runs":
                    return cache
                return [{**j, "check_run_url": f"https://api.github.com/repos/owner/project/check-runs/{i}"} for i, j in enumerate(jobs)]
            def check(self, head, state, external, message, check_id=None, name=None):
                value = {"id": check_id or len(cache) + 1, "name": name, "app": {"id": 69}, "external_id": external}
                if check_id:
                    cache[0] = value
                else:
                    cache.append(value)
        gh = Fake()
        contract = {"script": "approved"}
        proof["contract"] = m.digest(contract)
        run["status"] = "in_progress"
        with mock.patch.object(m, "api_log_evidence", side_effect=lambda _, job, marker: proof if marker == "CONTROL" else "m"):
            self.assertFalse(m.evaluate_ci(gh, {"ci": [workflow]}, expected, contract))
            self.assertFalse(m.evaluate_ci(gh, {"ci": [workflow]}, expected, contract))
            self.assertEqual(gh.dispatches, 1)
            run["status"] = "completed"
            self.assertTrue(m.evaluate_ci(gh, {"ci": [workflow]}, expected, contract))
            run["run_attempt"] = 3
            run["status"] = "in_progress"
            self.assertFalse(m.evaluate_ci(gh, {"ci": [workflow]}, expected, contract))
            self.assertEqual(gh.dispatches, 1)

    def test_npm_validation_uses_base_script_and_base_path(self):
        with tempfile.TemporaryDirectory() as directory:
            base, candidate = Path(directory) / "base", Path(directory) / "candidate"
            (base / "scripts").mkdir(parents=True)
            (candidate / "scripts").mkdir(parents=True)
            (base / "package.json").write_text(json.dumps({"scripts": {"verify": "node scripts/verify.mjs"}}))
            (candidate / "package.json").write_text(json.dumps({"scripts": {"verify": "true"}}))
            (base / "scripts/verify.mjs").write_text("process.exit(23);\n")
            (candidate / "scripts/verify.mjs").write_text("process.exit(0);\n")
            result = subprocess.run(["python3", "-I", str(self.project / ".github/scripts/merge-preparation.py"), "npm-script",
                                     "--root", str(base), "--candidate", str(candidate), "--name", "verify"], capture_output=True)
            self.assertEqual(result.returncode, 23, result.stderr)

    def test_generated_workflows_isolate_base_and_candidate_and_pass_actionlint(self):
        cases = [("use_python=true",), ("use_rust=true",), ("use_chrome_extension=true",),
                 ("use_tauri=true", "use_gh_actions_tauri_build=true"),
                 ("use_docker=true", "use_gh_actions_docker_release=true", "use_gh_actions_docker_quality=true"),
                 ("use_docker=true", "use_gh_actions_docker_project_pipeline=true"),
                 ("use_docker=true", "use_aws_ecr=true", "aws_account_id=123456789012", "aws_region=ap-northeast-1", "use_gh_actions_docker_release=true")]
        for answers in cases:
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as directory:
                project = Path(directory) / "project"
                result = subprocess.run(["copier", "copy", "--trust", "--defaults", *sum((["-d", a] for a in answers), []), str(ROOT), str(project)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                policy = json.loads((project / ".github/merge-preparation.json").read_text())
                for workflow in policy["ci"]:
                    text = (project / workflow["path"]).read_text()
                    self.assertNotIn("  pull_request:", text)
                    self.assertNotIn("secrets.", text)
                    self.assertNotIn("environment:", text)
                    self.assertIn("path: trusted-control", text)
                    self.assertIn("path: candidate", text)
                    self.assertIn("persist-credentials: false", text)
                    self.assertIn("needs: control", text)
                self.assertFalse((project / ".github/workflows/pr-tag-check.yml").exists())
                self.assertFalse((project / ".github/workflows/docker-project-pr-check.yml").exists())
                files = sorted((project / ".github/workflows").glob("*.yml"))
                lint = subprocess.run(["actionlint", "-ignore", '^unexpected key "queue" for "concurrency" section', *map(str, files)], capture_output=True, text=True)
                self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)


if __name__ == "__main__":
    unittest.main()
