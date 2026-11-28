import importlib.util
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import yaml

from template_tests import test_template

TARGETS = {
    "x86_64-unknown-linux-gnu": "ubuntu-latest",
    "x86_64-pc-windows-msvc": "windows-latest",
    "aarch64-pc-windows-msvc": "windows-11-arm",
    "aarch64-apple-darwin": "macos-latest",
}


class NativePullRequestTemplateTest(unittest.TestCase):
    def setUp(self):
        self.helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)

    def copy(self, *answers):
        result, root = self.helper.copy_template(*answers)
        self.assertEqual(result.returncode, 0, result.stdout)
        return root

    @staticmethod
    def workflow(root, language):
        return root / f".github/workflows/{language}-quality-checks.yml"

    @staticmethod
    def load_driver(root):
        spec = importlib.util.spec_from_file_location(
            "rust_native_pr", root / ".github/scripts/rust-native-pr.py"
        )
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        return driver

    def lint(self, workflow):
        if shutil.which("actionlint"):
            result = subprocess.run(
                ["actionlint", str(workflow)],
                cwd=workflow.parents[2],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_checks_are_opt_in_without_replacing_the_existing_job(self):
        for language in ("rust", "tauri"):
            with self.subTest(language=language):
                root = self.copy(f"use_{language}=true")
                workflow = yaml.safe_load(self.workflow(root, language).read_text())
                self.assertEqual(set(workflow["jobs"]), {f"{language}-quality-checks"})
                self.assertFalse((root / ".github/scripts/rust-native-pr.py").exists())
                self.assertFalse((root / "docs/native-pr-checks.md").exists())

    def test_rust_target_matrix_and_release_are_consistent(self):
        for targets, release in ((list(TARGETS)[:1], False), (list(TARGETS), True)):
            with self.subTest(targets=targets):
                args = [
                    "use_rust=true",
                    "use_gh_actions_native_pr=true",
                    "rust_release_targets=" + json.dumps(targets),
                ]
                if release:
                    args.append("use_gh_actions_release=true")
                project = self.copy(*args)
                path = self.workflow(project, "rust")
                workflow = yaml.safe_load(path.read_text())
                self.assertEqual(workflow["permissions"], {"contents": "read"})
                jobs = workflow["jobs"]
                self.assertEqual(set(jobs), {"rust-quality-checks", "native-pr-checks"})
                native = jobs["native-pr-checks"]
                expected = [{"target": target, "runner": TARGETS[target]} for target in targets]
                self.assertEqual(native["strategy"]["matrix"]["include"], expected)
                self.assertEqual(native["steps"][0]["with"], {"persist-credentials": False})
                self.assertIn("rust-native-pr.py", path.read_text())
                self.assertNotIn("secrets.", path.read_text())
                driver = self.load_driver(project)
                self.assertEqual(driver.CONFIGURED_TARGETS, targets)
                self.assertEqual({target: data[0] for target, data in driver.RUST_TARGETS.items()}, TARGETS)
                self.assertEqual((project / ".github/release.json").exists(), release)
                self.lint(path)

    def test_tauri_native_jobs_use_locked_builds_without_publication(self):
        root = self.copy("use_tauri=true", "use_gh_actions_native_pr=true")
        path = self.workflow(root, "tauri")
        workflow = yaml.safe_load(path.read_text())
        native = workflow["jobs"]["native-pr-checks"]
        self.assertEqual(native["strategy"]["matrix"]["include"], [
            {"target": target, "runner": runner} for target, runner in list(TARGETS.items())[1:]
        ])
        self.assertEqual(workflow["jobs"]["tauri-quality-checks"]["runs-on"], "ubuntu-latest")
        rendered = path.read_text()
        for command in ("npm ci", "npm run build", "cargo clippy --locked", "cargo test --locked"):
            self.assertIn(command, rendered)
        self.assertNotIn("release.py", rendered)
        self.assertNotIn("secrets.", rendered)
        self.assertFalse((root / ".github/scripts/rust-native-pr.py").exists())
        self.lint(path)

    def test_hook_nonzero_is_not_masked(self):
        root = self.copy(
            "use_rust=true",
            "use_gh_actions_native_pr=true",
            "use_native_pr_hook=true",
        )
        path = self.workflow(root, "rust")
        native = yaml.safe_load(path.read_text())["jobs"]["native-pr-checks"]
        hooks = [(s["name"], s["run"]) for s in native["steps"] if "project" in s["name"]]
        self.assertEqual(hooks, [
            ("Run project preflight", "bash scripts/native-pr-hook.sh before"),
            ("Run project native verification", "bash scripts/native-pr-hook.sh after"),
        ])
        self.assertTrue(all(not s.get("continue-on-error") for s in native["steps"]))
        script = root / "scripts/native-pr-hook.sh"
        script.parent.mkdir()
        script.write_text('case "$1" in before|after) exit 23 ;; *) exit 24 ;; esac\n')
        for _, run in hooks:
            self.assertEqual(subprocess.run(run.split(), cwd=root, check=False).returncode, 23)

    def test_release_policy_hooks_reused_and_target_drift_rejected(self):
        root = self.copy(
            "use_rust=true",
            "use_gh_actions_native_pr=true",
            "use_gh_actions_release=true",
        )
        driver = self.load_driver(root)
        policy_path = root / ".github/release.json"
        policy = json.loads(policy_path.read_text())
        package_hook = ["python", "scripts/fixture.py"]
        expected_target = driver.CONFIGURED_TARGETS[0]
        rust = policy["publication"]["rust"]
        rust.update(stage_command=package_hook, smoke_args=["--version"])
        policy_path.write_text(json.dumps(policy))
        self.assertEqual(driver.selected_spec(root, expected_target), {
            "targets": [expected_target], "stage_command": package_hook, "smoke_args": ["--version"],
        })
        rust["targets"] = [list(TARGETS)[-1]]
        policy_path.write_text(json.dumps(policy))
        with self.assertRaisesRegex(driver.PreparationError, "Copier answers"):
            driver.selected_spec(root, expected_target)

    def test_driver_does_not_keep_unverified_artifact_or_generate_lockfile(self):
        root = self.copy("use_rust=true", "use_gh_actions_native_pr=true")
        driver = self.load_driver(root)
        target = driver.CONFIGURED_TARGETS[0]
        output = root / "retained"
        with mock.patch.object(driver, "run") as run:
            with self.assertRaisesRegex(driver.PreparationError, "Commit Cargo.lock"):
                driver.verify(root, target, output)
            run.assert_not_called()
        (root / "Cargo.lock").write_text("version = 4\n")

        def build(_, plan, sha, target, directory):
            self.assertEqual(plan["release_tag"], "pr-" + sha[:12])
            (Path(directory) / target).mkdir()

        with (
            mock.patch.object(driver, "run", return_value=b"a" * 40 + b"\n"),
            mock.patch.object(driver, "build_rust_distribution", side_effect=build),
            mock.patch.object(
                driver, "rust_deliverables", side_effect=driver.PreparationError("bad checksum")
            ),
        ):
            with self.assertRaisesRegex(driver.PreparationError, "bad checksum"):
                driver.verify(root, target, output)
        self.assertFalse(output.exists())

    def test_copier_update_enables_native_job_and_preserves_project_hook_and_stage(self):
        template = self.helper.copy_template_repository()
        self.helper.commit_repository(template, "template")
        project = self.helper.create_versioned_project(
            template, "use_rust=true", "use_gh_actions_release=true"
        )
        original_job = yaml.safe_load(self.workflow(project, "rust").read_text())["jobs"]["rust-quality-checks"]
        script = project / "scripts/native-pr-hook.sh"
        script.parent.mkdir()
        script.write_text("# project-owned native check\n")
        policy_file = project / ".github/release.json"
        policy_file.write_text(policy_file.read_text().replace(
            '"stage_command": []', '"stage_command": ["python", "scripts/stage.py"]'
        ))
        self.helper.commit_repository(project, "project-owned extensions")
        result = self.helper.update_versioned_project(project,
            "use_gh_actions_native_pr=true",
            "use_native_pr_hook=true",
            "rust_release_targets=" + json.dumps(list(TARGETS)),
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        jobs = yaml.safe_load(self.workflow(project, "rust").read_text())["jobs"]
        self.assertEqual(jobs["rust-quality-checks"], original_job)
        self.assertIn("native-pr-checks", jobs)
        self.assertEqual(script.read_text(), "# project-owned native check\n")
        policy = json.loads(policy_file.read_text())["publication"]["rust"]
        self.assertEqual(policy["stage_command"], ["python", "scripts/stage.py"])
        self.assertEqual(policy["targets"], list(TARGETS))
        self.assertFalse(list(project.rglob("*.rej")))
