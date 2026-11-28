"""Offline Copier fixtures for shared Docker quality and publication."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DOCKER = {"use_python": False, "use_docker": True}


class DockerCommonizationTest(unittest.TestCase):
    def render(self, **options):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        project = Path(directory) / "project"
        answers = self.write_answers(Path(directory), DOCKER | options)
        self.copy(answers, project)
        return project

    def write_answers(self, directory, answers):
        result = directory / "answers.json"
        result.write_text(json.dumps(answers))
        return result

    def copy(self, answers, project):
        subprocess.run(
            ["copier", "copy", "--trust", "--defaults", "--skip-tasks",
             "--data-file", str(answers), str(ROOT), str(project)],
            check=True, capture_output=True, text=True,
        )

    def lint(self, workflow, *, queue=False):
        if shutil.which("actionlint"):
            # Older actionlint has only this known diagnostic on the existing
            # release queue syntax; all other diagnostics still fail.
            options = ["-ignore", 'unexpected key "queue"'] if queue else []
            result = subprocess.run(
                ["actionlint", *options, str(workflow)], text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @staticmethod
    def workflows(project, name):
        return project / ".github" / "workflows" / f"{name}.yml"

    def test_default_docker_quality_keeps_single_job(self):
        project = self.render(use_gh_actions_docker_quality=True)
        workflow = self.workflows(project, "docker-quality-checks")
        text = workflow.read_text()
        self.assertIn("  docker-quality-checks:\n    runs-on: ubuntu-latest", text)
        self.assertIn("      contents: read".lstrip(), text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("matrix:", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("--network none", text)
        self.assertNotIn("docker-quality-hook.sh", text)
        self.lint(workflow)

    def test_multi_arch_test_target_and_hooks(self):
        for arm in ("native", "qemu"):
            with self.subTest(arm=arm):
                project = self.render(
                    use_gh_actions_docker_quality=True,
                    docker_quality_platforms=["linux/amd64", "linux/arm64"],
                    docker_quality_arm_mode=arm,
                    docker_quality_test_target="tests",
                    docker_quality_smoke_offline=True,
                    use_docker_quality_hook=True,
                    dockerfile_path="build/Dockerfile",
                    docker_build_context="build",
                    docker_smoke_command="python --version",
                )
                workflow = self.workflows(project, "docker-quality-checks")
                text = workflow.read_text()
                self.assertIn("- platform: linux/amd64", text)
                self.assertIn("- platform: linux/arm64", text)
                self.assertEqual("ubuntu-24.04-arm" in text, arm == "native")
                self.assertEqual("docker/setup-qemu-action@" in text, arm == "qemu")
                self.assertEqual(text.count("          push: false"), 2)
                self.assertEqual(text.count('          context: "build"'), 2)
                self.assertTrue((project / "build" / ".dockerignore").is_file())
                self.assertIn('DOCKER_TEST_TARGET: "tests"', text)
                self.assertIn("--network none --entrypoint sh", text)
                self.assertNotIn("--network none", text.split("Run Docker smoke test")[0])
                self.assertIn("run: bash -- .github/scripts/docker-quality-hook.sh before", text)
                self.assertIn("run: bash -- .github/scripts/docker-quality-hook.sh after", text)
                self.assertIn("if: always() && steps.checkout.outcome == 'success'", text)
                self.assertIn("run: bash -- .github/scripts/docker-quality-hook.sh cleanup", text)
                self.assertIn("timeout-minutes: 5", text)
                self.assertNotIn("continue-on-error", text)
                self.assertNotIn("secrets.", text)
                self.assertIn("  docker-quality-checks:\n    needs: docker-quality-platforms", text)
                self.assertIn('run: test "$PLATFORM_RESULT" = success', text)
                self.lint(workflow)

    def release(self, project):
        spec = importlib.util.spec_from_file_location(
            "docker_common_release", project / ".github/scripts/release.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, json.loads((project / ".github/release.json").read_text())

    def test_upstream_revision_defaults_to_all_merges_and_r0(self):
        project = self.render(
            use_gh_actions_docker_project_pipeline=True,
            docker_project_version_scheme="upstream-revision",
            docker_project_release_paths=["Dockerfile", ".github/scripts/docker-image-project.sh"],
        )
        controller, policy = self.release(project)
        self.assertEqual(policy["version"]["revision_path"], "revision")
        self.assertNotIn("revision_policy", policy["version"])
        self.assertNotIn("    paths:", self.workflows(project, "docker-project-release").read_text())
        self.assertEqual(
            controller.choose(policy, "3.4.5", None, None, lambda _: [],
                              base_revision="r9", input_version="3.5.0")[2]["release_tag"],
            "3.5.0",
        )
        plan = controller.publication(policy, "3.5.0", "r3")
        self.assertEqual(plan["images"][0]["tag"], "3.5.0-r3")
        self.assertEqual(plan["latest_image"], "extended")
        self.assertIn(".github/scripts/docker-image-project.sh", plan["release_paths"])

    def test_worker_compatible_authored_plan_and_push_scope(self):
        project = self.render(
            use_gh_actions_docker_project_pipeline=True,
            docker_project_version_scheme="upstream-revision",
            docker_project_release_layout="base-process",
            docker_project_authored_revision=True,
            docker_project_filter_release_push=True,
            docker_project_release_paths=[
                "images/base/Dockerfile", "images/process/Dockerfile",
                ".github/scripts/docker-image-project.sh",
            ],
        )
        controller, policy = self.release(project)
        self.assertEqual(policy["version"]["revision_policy"], "authored")
        workflow_path = self.workflows(project, "docker-project-release")
        workflow = workflow_path.read_text()
        self.lint(workflow_path, queue=True)
        self.assertIn('      - "version"', workflow)
        self.assertIn('      - "revision"', workflow)
        self.assertIn('      - "images/base/Dockerfile"', workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        version = "3.9.5-python3.14"
        for revision, suffix in (("r0", ""), ("r1", "-r1")):
            plan = controller.publication(policy, version, revision)
            self.assertEqual(plan["release_tag"], version + suffix)
            self.assertEqual([image["tag"] for image in plan["images"]],
                             [version + "-base" + suffix, version + "-process" + suffix])
            self.assertFalse(plan["is_prerelease"])
            self.assertIsNone(plan["latest_image"])
        git = mock.Mock()
        git.blob.return_value = b"r1\n"
        git.text.return_value = "tree-object"
        git.commit.return_value = "numbering-commit"
        git.tag_object.return_value = "tag-object"
        gh = mock.Mock()
        reserved = {"release_sha": "numbering-commit"}
        with (
            mock.patch.object(controller, "preparation_input",
                              return_value={"main": "main-sha", "authored": True}),
            mock.patch.object(controller, "policy_at", return_value=policy),
            mock.patch.object(controller, "read_version", return_value=version),
            mock.patch.object(controller, "remote_collisions", return_value=[]),
            mock.patch.object(controller, "record_at"),
            mock.patch.object(controller, "prepared", return_value=reserved),
            mock.patch.object(controller, "choose") as auto_number,
            mock.patch.object(controller, "update_versions") as edit_source,
        ):
            self.assertEqual(controller.prepare(git, gh, "123"), reserved)
        auto_number.assert_not_called()
        edit_source.assert_not_called()
        git.text.assert_called_once_with("rev-parse", "main-sha^{tree}")
        self.assertEqual(git.commit.call_args.args[:2], ("tree-object", "main-sha"))
        git.tag_object.assert_called_once_with("numbering-commit", version + "-r1")
        git.command.assert_called_once_with(
            "push", "--atomic", "origin", "numbering-commit:refs/heads/main",
            f"tag-object:refs/tags/{version}-r1",
        )
        git.command.reset_mock()
        with (
            mock.patch.object(controller, "preparation_input",
                              return_value={"main": "main-sha", "authored": True}),
            mock.patch.object(controller, "policy_at", return_value=policy),
            mock.patch.object(controller, "read_version", return_value=version),
            mock.patch.object(controller, "remote_collisions", return_value=["base"]),
            self.assertRaisesRegex(controller.PreparationError, "already exists"),
        ):
            controller.prepare(git, gh, "123")
        git.command.assert_not_called()

        current = {"Run": "123", "commit": "recorded-commit"}
        git.fetch_main.return_value = current["commit"]
        gh.repo.return_value = {"default_branch": "main"}
        with (
            mock.patch.object(controller, "records", return_value=[current]),
            mock.patch.object(controller, "policy_at", return_value=policy),
            mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch"}),
        ):
            self.assertEqual(controller.preparation_input(git, gh, "456"), {"record": current})

    def test_optional_tag_prefix_and_dockerhub_credential_migration(self):
        project = self.render(
            use_gh_actions_docker_release=True,
            docker_release_tag_prefix="v",
            use_dockerhub_legacy_token=True,
        )
        controller, policy = self.release(project)
        plan = controller.publication(policy, "1.2.3")
        self.assertEqual(plan["release_tag"], "v1.2.3")
        self.assertEqual(plan["images"][0]["tag"], "1.2.3")
        workflow = self.workflows(project, "docker-release").read_text()
        self.assertIn("${{ secrets.DOCKERHUB_TOKEN || secrets.DOCKER_TOKEN }}", workflow)
        self.assertNotIn("${{ secrets.DOCKERHUB_TOKEN }}", workflow)
        self.assertFalse((project / "_dockerhub_auth.yml").exists())
        self.assertFalse((project / "_release_config_paths.yml").exists())
