import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

import test_template


class HomebrewNotificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)

    def render(self, *extra: str):
        result, project = self.helper.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true",
            "use_gh_actions_tauri_homebrew_notify=true",
            "homebrew_tap_repository=example-org/homebrew-desktop", *extra,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        workflow = yaml.safe_load((project / ".github/workflows/tauri-build.yml").read_text())
        return project, workflow

    def test_notification_is_default_off(self) -> None:
        for answers in ((), ("use_tauri=true",),
                        ("use_tauri=true", "use_gh_actions_tauri_build=true")):
            with self.subTest(answers=answers):
                result, project = self.helper.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                workflow = project / ".github/workflows/tauri-build.yml"
                if workflow.exists():
                    self.assertNotIn("notify-homebrew:", workflow.read_text())
                self.assertFalse((project / "docs/homebrew-tap-notification.md").exists())
                self.assertNotIn("homebrew_tap_repository:", (project / ".copier-answers.yml").read_text())

    def test_notification_requires_tauri_distribution(self) -> None:
        for answers in (("use_gh_actions_tauri_homebrew_notify=true",),
                        ("use_tauri=true", "use_gh_actions_tauri_homebrew_notify=true")):
            with self.subTest(answers=answers):
                result, project = self.helper.copy_template(*answers)
                if result.returncode:
                    self.assertIn("Homebrew通知には", result.stdout)
                else:
                    self.assertFalse((project / ".github/workflows/tauri-build.yml").exists())
                    self.assertFalse((project / "docs/homebrew-tap-notification.md").exists())

    def test_generated_job_has_completion_stability_and_permission_guards(self) -> None:
        project, workflow = self.render("homebrew_tap_workflow=refresh-desktop.yaml")
        job = workflow["jobs"]["notify-homebrew"]
        self.assertEqual(job["needs"], ["preflight", "promote-latest"])
        self.assertEqual(job["permissions"], {})
        for required in ("github.ref == 'refs/heads/main'", "needs.preflight.result == 'success'",
                         "needs.promote-latest.result == 'success'",
                         "!contains(needs.preflight.outputs.version, '-')",
                         "!contains(needs.preflight.outputs.version, '+')",
                         "vars.HOMEBREW_TAP_NOTIFY_ENABLED == 'true'"):
            self.assertIn(required, job["if"])
        token = job["steps"][0]
        self.assertEqual(token["with"], {
            "client-id": "${{ vars.HOMEBREW_TAP_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.HOMEBREW_TAP_APP_PRIVATE_KEY }}",
            "owner": "example-org", "repositories": "homebrew-desktop", "permission-actions": "write",
        })
        self.assertRegex(token["uses"], r"^actions/create-github-app-token@[0-9a-f]{40}$")
        self.assertNotIn("skip-token-revoke", token["with"])
        self.assertEqual(job["steps"][1]["env"], {
            "GH_TOKEN": "${{ steps.tap-token.outputs.token }}",
            "TAP_REPOSITORY": "example-org/homebrew-desktop", "TAP_WORKFLOW": "refresh-desktop.yaml",
        })
        self.assertNotIn("mizucopo", json.dumps(job))
        text = (project / ".github/workflows/tauri-build.yml").read_text()
        self.assertEqual(workflow["permissions"], {"contents": "read", "pull-requests": "read"})
        expected_permissions = {
            "preflight": {"contents": "write", "pull-requests": "read"},
            "quality": workflow["permissions"],
            "build": workflow["permissions"],
            "publish": {"contents": "write"},
            "promote-latest": {"contents": "write"},
            "notify-homebrew": {},
        }
        self.assertEqual({
            name: generated_job.get("permissions", workflow["permissions"])
            for name, generated_job in workflow["jobs"].items()
        }, expected_permissions)
        self.assertNotIn("\n\n\n", text[text.index("\n  notify-homebrew:"):])
        doc = (project / "docs/homebrew-tap-notification.md").read_text()
        for expected in ("example-org/homebrew-desktop", "refresh-desktop.yaml",
                         "Copier-managed", "copier update", "Actions write", "prerelease"):
            self.assertIn(expected, doc)

    def test_destination_answers_reject_urls_paths_and_injection(self) -> None:
        for answer in ("homebrew_tap_repository=", "homebrew_tap_repository=https://github.com/org/tap",
                       "homebrew_tap_repository=org/tap/extra", "homebrew_tap_repository=org/tap\n",
                       "homebrew_tap_repository=org/tap;echo bad", "homebrew_tap_repository=org/../tap",
                       "homebrew_tap_workflow=../../workflow.yml", "homebrew_tap_workflow=update.yml\n",
                       "homebrew_tap_workflow=update.yml?ref=evil", 'homebrew_tap_workflow=bad".yml'):
            with self.subTest(answer=answer):
                result, _ = self.helper.copy_template(
                    "use_tauri=true", "use_gh_actions_tauri_build=true",
                    "use_gh_actions_tauri_homebrew_notify=true",
                    "homebrew_tap_repository=example-org/homebrew-desktop", answer,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("Homebrew", result.stdout)

    def run_notification(self, success_at: int):
        project, workflow = self.render()
        script = workflow["jobs"]["notify-homebrew"]["steps"][1]["run"]
        syntax = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        mock_bin = project / "mock-bin"
        mock_bin.mkdir()
        gh = mock_bin / "gh"
        gh.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
path = Path(os.environ["MOCK_CALLS"])
calls = json.loads(path.read_text()) if path.exists() else []
body = json.load(sys.stdin)
calls.append({"args": sys.argv[1:], "body": body})
path.write_text(json.dumps(calls))
# Enforce our string wire-format contract before simulating API failures.
if not all(isinstance(value, str) for value in body["inputs"].values()):
    sys.exit("notification inputs must use string wire values")
raise SystemExit(0 if len(calls) >= int(os.environ["MOCK_SUCCESS_AT"]) else 1)
''')
        gh.chmod(0o755)
        sleep = mock_bin / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)
        calls = project / "calls.json"
        result = subprocess.run(["bash", "-e", "-o", "pipefail"], input=script, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=project,
                                env=dict(os.environ, PATH=str(mock_bin) + os.pathsep + os.environ["PATH"],
                                         TAP_REPOSITORY="example-org/homebrew-desktop",
                                         TAP_WORKFLOW="refresh-desktop.yaml", GH_TOKEN="test-only-token",
                                         MOCK_CALLS=str(calls), MOCK_SUCCESS_AT=str(success_at)))
        recorded = json.loads(calls.read_text())
        for call in recorded:
            self.assertEqual(call["body"], {"ref": "main", "inputs": {"apply": "true"}})
            self.assertIn("/repos/example-org/homebrew-desktop/actions/workflows/refresh-desktop.yaml/dispatches",
                          call["args"])
            self.assertIn("POST", call["args"])
        self.assertNotIn("test-only-token", result.stdout + calls.read_text())
        return result, recorded

    def test_successful_notification_sends_one_bounded_request(self) -> None:
        result, calls = self.run_notification(1)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(calls), 1)

    def test_notification_retries_transient_failure(self) -> None:
        result, calls = self.run_notification(3)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(len(calls), 3)

    def test_notification_reports_persistent_failure(self) -> None:
        result, calls = self.run_notification(99)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertIn("Tap notification failed", result.stdout)

    def test_copier_update_enables_and_disables_without_losing_project_changes(self) -> None:
        template = self.helper.copy_template_repository()
        self.helper.commit_repository(template, "template with optional notification")
        notification_answers = (
            "use_gh_actions_tauri_homebrew_notify=true",
            "homebrew_tap_repository=example-org/homebrew-desktop",
            "homebrew_tap_workflow=refresh-desktop.yaml",
        )
        for operation, answers, enabled in (
            ("enable", notification_answers, True),
            ("update saved settings", (), True),
            ("disable", ("use_gh_actions_tauri_homebrew_notify=false",), False),
        ):
            with self.subTest(operation=operation):
                # Each case starts with its own clean project, even if another case fails.
                project = self.helper.create_versioned_project(
                    template, "use_tauri=true", "use_gh_actions_tauri_build=true",
                )
                path = project / ".github/workflows/tauri-build.yml"
                customized = path.read_text()
                for original, replacement in (
                    ("name: Tauri Distribution Release",
                     "# Project-specific release customization\nname: My Desktop Release"),
                    ("\n    if: needs.preflight.outputs.is_prerelease == 'false'\n",
                     "\n    if: needs.preflight.outputs.is_prerelease == 'false' "
                     "&& vars.DESKTOP_RELEASE_ENABLED == 'true'\n"),
                ):
                    self.assertEqual(customized.count(original), 1)
                    customized = customized.replace(original, replacement, 1)
                customized_workflow = yaml.safe_load(customized)
                self.assertEqual(
                    customized_workflow["jobs"]["preflight"]["outputs"]["is_prerelease"],
                    "${{ steps.metadata.outputs.is_prerelease }}",
                )
                path.write_text(customized)
                (project / "src/main.ts").write_text("// Existing project implementation\n")
                self.helper.commit_repository(project, "project-specific release and application")
                if operation != "enable":
                    preparation = self.helper.update_versioned_project(project, *notification_answers)
                    self.assertEqual(preparation.returncode, 0, preparation.stdout)
                    self.helper.commit_repository(project, "enable notification before update")
                status = self.helper.run_process(["git", "status", "--porcelain"], project)
                self.assertEqual(status.returncode, 0, status.stdout)
                self.assertEqual(status.stdout, "", "Copier update must start from a clean project")

                result = self.helper.update_versioned_project(project, *answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                workflow = yaml.safe_load(path.read_text())
                self.assertEqual("notify-homebrew" in workflow["jobs"], enabled)
                self.assertEqual((project / "docs/homebrew-tap-notification.md").exists(), enabled)
                self.assertIn("# Project-specific release customization", path.read_text())
                existing_jobs = {
                    name: job for name, job in workflow["jobs"].items() if name != "notify-homebrew"
                }
                self.assertEqual({**workflow, "jobs": existing_jobs}, customized_workflow)
                self.assertEqual((project / "src/main.ts").read_text(), "// Existing project implementation\n")
                saved_answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
                self.assertEqual(saved_answers["use_gh_actions_tauri_homebrew_notify"], enabled)
                if enabled:
                    self.assertEqual(saved_answers["homebrew_tap_repository"], "example-org/homebrew-desktop")
                    self.assertEqual(saved_answers["homebrew_tap_workflow"], "refresh-desktop.yaml")
                    self.assertEqual(workflow["jobs"]["notify-homebrew"]["steps"][1]["env"]["TAP_WORKFLOW"],
                                     "refresh-desktop.yaml")
                else:
                    self.assertEqual(path.read_text(), customized)
                    self.assertNotIn("homebrew_tap_repository", saved_answers)
                    self.assertNotIn("homebrew_tap_workflow", saved_answers)


if __name__ == "__main__":
    unittest.main()
