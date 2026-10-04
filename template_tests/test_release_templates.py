import json
import os
import subprocess
import unittest

import test_template
import yaml


class ReleaseTemplateTest(unittest.TestCase):
    def setUp(self):
        self.helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)

    def render(self, *answers):
        result, root = self.helper.copy_template(*answers)
        self.assertEqual(result.returncode, 0, result.stdout)
        return root

    def test_all_publication_workflows_reject_non_main_before_preparation(self):
        cases = [
            ("release.yml", ("use_gh_actions_release=true",)),
            (
                "docker-release.yml",
                ("use_docker=true", "use_gh_actions_docker_release=true"),
            ),
            (
                "docker-release.yml",
                (
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                    "use_aws_ecr=true",
                ),
            ),
            (
                "chrome-extension-release.yml",
                (
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                ),
            ),
            (
                "tauri-build.yml",
                ("use_tauri=true", "use_gh_actions_tauri_build=true"),
            ),
            (
                "docker-project-release.yml",
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_project_pipeline=true",
                ),
            ),
        ]
        for name, answers in cases:
            with self.subTest(name=name, answers=answers):
                root = self.render(*answers)
                workflow = yaml.safe_load(
                    (root / ".github/workflows" / name).read_text()
                )
                triggers = workflow.get("on", workflow.get(True))
                self.assertEqual(triggers["push"]["branches"], ["main"])
                self.assertIn("workflow_dispatch", triggers)
                prepare = workflow["jobs"]["prepare"]
                self.assertNotIn("if", prepare)
                self.assertFalse(prepare.get("continue-on-error"))
                guard = prepare["steps"][0]
                self.assertNotIn("if", guard)
                self.assertNotIn("uses", guard)
                self.assertFalse(guard.get("continue-on-error"))
                for event, ref, success in [
                    ("push", "refs/heads/main", True),
                    ("workflow_dispatch", "refs/heads/main", True),
                    ("workflow_dispatch", "refs/heads/feature", False),
                    ("workflow_dispatch", "refs/heads/main/feature", False),
                    ("workflow_dispatch", "refs/tags/main", False),
                    ("workflow_dispatch", "", False),
                ]:
                    with self.subTest(event=event, ref=ref):
                        result = subprocess.run(
                            ["bash", "-e", "-o", "pipefail"],
                            input=guard["run"],
                            cwd=root,
                            env={
                                **os.environ,
                                "GITHUB_EVENT_NAME": event,
                                "GITHUB_REF": ref,
                            },
                            text=True,
                            capture_output=True,
                        )
                        self.assertEqual(result.returncode == 0, success, result.stderr)
                        if not success:
                            self.assertIn(
                                "::error::Release requires main. Select the main branch",
                                result.stdout + result.stderr,
                            )
                for job_name, job in workflow["jobs"].items():
                    if job_name == "prepare":
                        continue
                    needs = job["needs"]
                    needs = [needs] if isinstance(needs, str) else needs
                    self.assertTrue(needs)
                    pending, ancestors = list(needs), set()
                    while pending:
                        dependency = pending.pop()
                        if dependency in ancestors:
                            continue
                        ancestors.add(dependency)
                        parents = workflow["jobs"][dependency].get("needs", [])
                        pending.extend([parents] if isinstance(parents, str) else parents)
                    self.assertIn("prepare", ancestors)
                    self.assertFalse(job.get("continue-on-error"))
                    if "always()" in job.get("if", ""):
                        self.assertIn("needs.preflight.result == 'success'", job["if"])

    def test_all_publication_jobs_checkout_numbered_commit(self):
        cases = [
            ("release.yml", ("use_python=true", "use_gh_actions_release=true")),
            (
                "release.yml",
                ("use_python=false", "use_rust=true", "use_gh_actions_release=true"),
            ),
            ("release.yml", ("use_tauri=true", "use_gh_actions_release=true")),
            (
                "docker-release.yml",
                (
                    "use_tauri=true",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
            ),
            (
                "docker-release.yml",
                ("use_docker=true", "use_gh_actions_docker_release=true"),
            ),
            (
                "docker-release.yml",
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                    "use_aws_ecr=true",
                ),
            ),
            (
                "chrome-extension-release.yml",
                (
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                ),
            ),
            (
                "tauri-build.yml",
                (
                    "use_tauri=true",
                    "use_gh_actions_tauri_build=true",
                    "use_gh_actions_tauri_homebrew_notify=true",
                    "homebrew_tap_repository=owner/homebrew-app",
                ),
            ),
            (
                "docker-project-release.yml",
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_project_pipeline=true",
                ),
            ),
        ]
        for name, answers in cases:
            with self.subTest(name=name, answers=answers):
                root = self.render(*answers)
                source = root / ".github/workflows" / name
                workflow = yaml.safe_load(source.read_text())
                self.assertEqual(
                    workflow["concurrency"],
                    {
                        "group": "release-main",
                        "cancel-in-progress": False,
                        "queue": "max",
                    },
                )
                jobs = workflow["jobs"]
                self.assertEqual(jobs["prepare"]["permissions"]["contents"], "write")
                for job_name, job in jobs.items():
                    if job_name == "prepare":
                        continue
                    for step in job["steps"]:
                        if step.get("uses", "").startswith("actions/checkout@"):
                            ref = step["with"]["ref"]
                            self.assertIn("outputs.release_sha", ref)
                            self.assertEqual(job["env"]["RELEASE_SHA"], ref)
                if "use_tauri=true" in answers and name != "tauri-build.yml":
                    commands = "\n".join(
                        step.get("run", "") for step in jobs["release"]["steps"]
                    )
                    self.assertIn("libwebkit2gtk-4.1-dev", commands)
                    self.assertIn("rustup show", commands)
                policy = json.loads((root / ".github/release.json").read_text())
                self.assertEqual(set(policy), {"version", "publication"})
                self.assertTrue((root / "docs/release.md").is_file())
                self.assertFalse((root / ".github/merge-preparation.json").exists())
                self.assertFalse(
                    (root / ".github/workflows/merge-preparation.yml").exists()
                )
                self.assertEqual((root / ".codex/project.md").stat().st_size, 0)
                result = subprocess.run(
                    ["actionlint", "-shellcheck=", str(source)],
                    capture_output=True,
                    text=True,
                )
                # Installed actionlint does not yet know the GitHub queue option.
                errors = [
                    line
                    for line in result.stdout.splitlines()
                    if 'unexpected key "queue" for "concurrency" section.' not in line
                    and not line.lstrip().startswith(
                        (
                            "^",
                            "queue:",
                            "|",
                            "10 |",
                            "11 |",
                            "12 |",
                            "13 |",
                            "14 |",
                            "15 |",
                            "16 |",
                        )
                    )
                    and line.strip()
                ]
                self.assertFalse(errors, result.stdout)

    def test_pr_ci_uses_local_quality_commands_and_read_only_permissions(self):
        for answers, name, expected in [
            (("use_python=true",), "pr-quality-checks.yml", "uv run task check"),
            (
                ("use_python=false", "use_chrome_extension=true"),
                "chrome-extension-quality-checks.yml",
                "npm run check",
            ),
            (("use_tauri=true",), "tauri-quality-checks.yml", "npm run check"),
            (
                ("use_python=false", "use_rust=true"),
                "rust-quality-checks.yml",
                "cargo test --all-targets --all-features",
            ),
        ]:
            with self.subTest(name=name):
                root = self.render(*answers)
                workflow = yaml.safe_load(
                    (root / ".github/workflows" / name).read_text()
                )
                self.assertEqual(workflow["permissions"], {"contents": "read"})
                steps = next(iter(workflow["jobs"].values()))["steps"]
                self.assertTrue(any(expected in step.get("run", "") for step in steps))
                self.assertFalse(any(step.get("continue-on-error") for step in steps))

    def test_chrome_uses_original_event_identity_and_validates_dist(self):
        root = self.render(
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
            "chrome_extension_version=1.2.3",
            "chrome_extension_release_notes=Version {version}.",
            "chrome_extension_release_package_root_directory=packages\\extension",
        )
        workflow = yaml.safe_load(
            (root / ".github/workflows/chrome-extension-release.yml").read_text()
        )
        job = workflow["jobs"]["release"]
        self.assertEqual(job["env"]["PACKAGE_ROOT"], "packages/extension")
        metadata = next(
            step["run"]
            for step in job["steps"]
            if step["name"] == "Prepare distribution metadata"
        )
        runner = root / "runner-temp"
        runner.mkdir()
        event = root / "event.json"
        output = root / "outputs"
        env = {
            **os.environ,
            "VERSION": "1.2.3",
            "NOTES_TEMPLATE": "Version {version}.",
            "RUNNER_TEMP": str(runner),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "new-owner/new-name",
            "GITHUB_EVENT_PATH": str(event),
        }
        event.write_text(
            json.dumps({"repository": {"full_name": "old-owner/original-name"}})
        )
        result = subprocess.run(
            ["bash", "-e"],
            input=metadata,
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("zip_name=original-name-1.2.3.zip", output.read_text())
        self.assertEqual((runner / "release-notes.md").read_text(), "Version 1.2.3.")
        for invalid in [
            {},
            {"repository": {"full_name": "invalid"}},
            {"repository": {"full_name": None}},
            *[
                {"repository": {"full_name": value}}
                for value in [
                    "",
                    "owner/",
                    "owner/project/extra",
                    "owner/project\n",
                    "owner/project#label",
                    "owner/project\\path",
                ]
            ],
        ]:
            event.write_text(json.dumps(invalid))
            output.unlink(missing_ok=True)
            result = subprocess.run(
                ["bash", "-e"],
                input=metadata,
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
        build = next(
            step
            for step in job["steps"]
            if step["name"] == "Build and validate distribution"
        )
        self.assertEqual(build["working-directory"], "${{ env.PACKAGE_ROOT }}")
        script = (
            build["run"]
            .split("node --input-type=module <<'NODE'\n", 1)[1]
            .split("\nNODE", 1)[0]
        )
        (root / "dist").mkdir()
        manifest = root / "dist/manifest.json"
        for version, success in [("1.2.3", True), ("1.2.4", False), (None, False)]:
            manifest.write_text(json.dumps({"version": version}))
            result = subprocess.run(
                ["node", "--input-type=module"],
                input=script,
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode == 0, success, result.stderr)


if __name__ == "__main__":
    unittest.main()
