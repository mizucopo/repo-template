import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock
from urllib import error

ROOT = Path(__file__).resolve().parents[1]
SINGLE = {
    "release_tag": "1.2.3-r1",
    "images": [{"name": "extended", "tag": "1.2.3-r1"}],
    "latest_image": "extended",
    "release_paths": ["version", "revision", "Dockerfile"],
}
MULTI = {
    "release_tag": "3.4.5-r2",
    "images": [
        {"name": "base", "tag": "3.4.5-base-r2"},
        {"name": "process", "tag": "3.4.5-process-r2"},
    ],
    "latest_image": None,
    "release_paths": ["version", "revision", "images/base/Dockerfile",
                      "images/process/Dockerfile"],
}


class DockerProjectPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.destination = Path(cls.temporary.name) / "project"
        result = subprocess.run(
            ["copier", "copy", "--trust", "--defaults",
             "-d", "use_python=false", "-d", "use_docker=true",
             "-d", "docker_registry=mizucopo",
             "-d", "docker_image_name=prefect-worker",
             "-d", "use_gh_actions_docker_project_pipeline=true",
             str(ROOT), str(cls.destination)],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if result.returncode:
            raise AssertionError(result.stdout)
        spec = importlib.util.spec_from_file_location(
            "docker_image_pipeline",
            cls.destination / ".github/scripts/docker-image-pipeline.py",
        )
        if spec is None or spec.loader is None:
            raise AssertionError("Could not import generated pipeline")
        cls.pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.pipeline)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_generated_workflows_and_helpers(self) -> None:
        workflows = self.destination / ".github/workflows"
        for name in ("release-classification.yml",
                     "docker-project-quality-checks.yml",
                     "docker-project-release.yml"):
            self.assertTrue((workflows / name).is_file(), name)
        for name in ("docker-release.yml", "docker-quality-checks.yml",
                     "pr-tag-check.yml"):
            self.assertFalse((workflows / name).exists(), name)
        for name in ("manage-docker-image-owner.sh", "release.py"):
            self.assertTrue((self.destination / ".github/scripts" / name).is_file())
        self.assertFalse(
            (self.destination / ".github/scripts/docker-image-project.sh").exists()
        )
        self.assertTrue((self.destination / "docs/docker-project-pipeline.md").is_file())
        self.assertTrue(
            (self.destination / "docs/examples/docker-project-n8n.sh").is_file()
        )
        self.assertTrue(
            (self.destination / "docs/examples/docker-project-worker.sh").is_file()
        )
        release = (workflows / "docker-project-release.yml").read_text()
        self.assertIn("group: release-main\n", release)
        self.assertIn("queue: max\n", release)
        self.assertNotIn("  resolve:\n", release)
        self.assertIn("promote_latest: ${{ steps.publish.outputs.promote_latest }}", release)
        self.assertIn("needs.release.outputs.promote_latest == 'true'", release)

    def test_incompatible_pipeline_answers_are_rejected(self) -> None:
        incompatible = [
            "use_gh_actions_docker_release=true",
            "use_gh_actions_docker_quality=true",
            "use_gh_actions_release=true",
            "use_aws_ecr=true",
        ]
        for answer in incompatible:
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    ["copier", "copy", "--trust", "--defaults",
                     "-d", "use_python=false", "-d", "use_docker=true",
                     "-d", "use_gh_actions_docker_project_pipeline=true",
                     "-d", answer, str(ROOT), str(Path(directory) / "project")],
                    check=False, text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("project Docker pipeline", result.stdout)

    def test_single_and_multi_image_plans_validate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            hook = Path(directory) / "hook.sh"
            hook.write_text("# placeholder\n")
            for source in (SINGLE, MULTI):
                with self.subTest(images=len(source["images"])):
                    with (
                        mock.patch.object(self.pipeline, "HOOK", hook),
                        mock.patch.object(self.pipeline, "repository", return_value="mizucopo/test"),
                        mock.patch.object(self.pipeline, "command", return_value=json.dumps(source)),
                    ):
                        plan = self.pipeline.validate_plan(source)
                    self.assertEqual(plan["release_tag"], source["release_tag"])
                    self.assertEqual(plan["images"], source["images"])
                    self.assertEqual(plan["latest_image"], source["latest_image"])

    def test_example_hooks_use_fixed_plan_for_release_notes(self) -> None:
        examples = {
            "n8n": ("1.2.3", SINGLE),
            "worker": ("3.4.5", MULTI),
        }
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            (workdir / "revision").write_text("r1\n")
            for name, (version, expected) in examples.items():
                with self.subTest(name=name):
                    (workdir / "version").write_text(version + "\n")
                    hook = (
                        self.destination / "docs/examples" / f"docker-project-{name}.sh"
                    )
                    plan = deepcopy(expected)
                    plan_path = workdir / "plan.json"
                    plan_path.write_text(json.dumps(plan))
                    notes = subprocess.run(
                        ["bash", str(hook), "notes"], cwd=workdir,
                        env={**os.environ, "IMAGE_REPOSITORY": "mizucopo/example",
                             "DOCKER_RELEASE_PLAN": str(plan_path)},
                        check=False, text=True, capture_output=True,
                    )
                    self.assertEqual(notes.returncode, 0, notes.stderr)
                    for image in plan["images"]:
                        self.assertIn(
                            f"mizucopo/example:{image['tag']}", notes.stdout
                        )

    def test_invalid_plans_fail(self) -> None:
        changes = [
            {"release_tag": "latest"},
            {"release_tag": "-foo"},
            {"release_tag": "--generate-notes"},
            {"unexpected_field": "value"},
            {"images": [{"name": "base", "tag": "bad/tag"}]},
            {"images": [{"name": "base", "tag": "x"},
                        {"name": "process", "tag": "x"}]},
            {"latest_image": "missing"},
            {"latest_image": []},
            {"release_paths": ["../Dockerfile", "version"]},
            {"images": [{"name": "base", "tag": "foo..bar"}]},
            {"images": [{"name": "base", "tag": "foo."}]},
            {"images": [{"name": "base", "tag": "foo.lock"}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            hook = Path(directory) / "hook.sh"
            hook.write_text("# placeholder\n")
            for change in changes:
                with self.subTest(change=change):
                    source = {**deepcopy(MULTI), **change}
                    with (
                        mock.patch.object(self.pipeline, "HOOK", hook),
                        mock.patch.object(self.pipeline, "repository", return_value="mizucopo/test"),
                        mock.patch.object(self.pipeline, "command", return_value=json.dumps(source)),
                    ):
                        with self.assertRaises(self.pipeline.PipelineError):
                            self.pipeline.validate_plan(source)

    def test_project_hook_receives_no_publication_credentials(self) -> None:
        plan = {**deepcopy(MULTI), "image_repository": "mizucopo/prefect-worker"}
        with (
            mock.patch.dict(os.environ, {
                "GH_TOKEN": "github-secret",
                "DOCKERHUB_TOKEN": "docker-secret",
            }),
            mock.patch.object(self.pipeline, "command") as command,
        ):
            self.pipeline.hook("quality", plan)
        hook_env = command.call_args.kwargs["env"]
        self.assertNotIn("GH_TOKEN", hook_env)
        self.assertNotIn("DOCKERHUB_TOKEN", hook_env)
        self.assertIn("DOCKER_RELEASE_PLAN", hook_env)

    def test_numbered_runtime_releases_do_not_require_plain_version_or_path_changes(self) -> None:
        for paths in (["pyproject.toml"], ["Cargo.toml"], ["package.json", "src/manifest.json"]):
            with self.subTest(paths=paths), mock.patch.object(self.pipeline, "repository", return_value="owner/image"):
                plan = self.pipeline.validate_plan({**deepcopy(MULTI), "release_paths": paths})
                with (
                    mock.patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "push", "RELEASE_SHA": "a" * 40}),
                    mock.patch.object(self.pipeline, "local_tag_commit", return_value="a" * 40),
                    mock.patch.object(self.pipeline, "github_release_exists", return_value=True),
                    mock.patch.object(self.pipeline, "hub_token", return_value="token"),
                    mock.patch.object(self.pipeline, "image_exists", return_value=True),
                    mock.patch.object(self.pipeline, "command") as command,
                    mock.patch.object(self.pipeline, "output"),
                ):
                    self.pipeline.release(plan, is_prerelease=False)
                command.assert_any_call("bash", str(self.pipeline.OWNER), "verify", "3.4.5-base-r2")

    def test_partial_release_resumes_in_dependency_order(self) -> None:
        plan = {**deepcopy(MULTI), "image_repository": "mizucopo/prefect-worker"}
        env = {"GITHUB_REF": "refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch",
               "RELEASE_SHA": "a" * 40,
               "GIT_USER_NAME": "release",
               "GIT_USER_EMAIL": "release@example.com"}
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(self.pipeline, "local_tag_commit", return_value="a" * 40),
            mock.patch.object(self.pipeline, "github_release_exists", return_value=False),
            mock.patch.object(self.pipeline, "hub_token", return_value="token"),
            mock.patch.object(self.pipeline, "image_exists", side_effect=[True, False, True]),
            mock.patch.object(self.pipeline, "docker_login") as login,
            mock.patch.object(self.pipeline, "command") as command,
            mock.patch.object(self.pipeline, "hook", return_value="notes") as hook,
            mock.patch.object(self.pipeline, "output") as output,
        ):
            self.pipeline.release(plan, is_prerelease=False)
        login.assert_called_once()
        hook.assert_any_call(
            "publish", plan, "process", "mizucopo/prefect-worker:3.4.5-process-r2"
        )
        self.assertEqual(sum(call.args[0] == "publish"
                             for call in hook.call_args_list), 1)
        verify = command.call_args_list.index(
            mock.call("bash", str(self.pipeline.OWNER), "verify", "3.4.5-base-r2")
        )
        record = command.call_args_list.index(
            mock.call("bash", str(self.pipeline.OWNER), "record", "3.4.5-process-r2")
        )
        self.assertLess(verify, record)
        self.assertFalse(any(call.args[:2] == ("git", "tag") for call in command.call_args_list))
        output.assert_called_with("promote_latest", "false")

    def test_long_builds_use_fresh_tokens_for_each_post_push_check(self) -> None:
        for source in (SINGLE, MULTI):
            for existing in range(len(source["images"]) + 1):
                with self.subTest(images=len(source["images"]), existing=existing):
                    plan = {**deepcopy(source), "image_repository": "mizucopo/example"}
                    published = {image["tag"] for image in plan["images"][:existing]}
                    now = 0

                    def image_exists(_plan, tag, token):
                        if now - token >= 600:
                            raise self.pipeline.PipelineError("Docker Hub HTTP 401")
                        return tag in published

                    def hook(operation, _plan, *args, **_kwargs):
                        nonlocal now
                        if operation == "publish":
                            now += 601
                            published.add(args[1].split(":")[-1])
                        return "notes"

                    complete = existing == len(plan["images"])
                    with (
                        mock.patch.dict(os.environ, {
                            "GITHUB_REF": "refs/heads/main", "RELEASE_SHA": "a" * 40,
                        }),
                        mock.patch.object(self.pipeline, "local_tag_commit", return_value="a" * 40),
                        mock.patch.object(self.pipeline, "github_release_exists", return_value=complete),
                        mock.patch.object(self.pipeline, "hub_token", side_effect=lambda: now),
                        mock.patch.object(self.pipeline, "image_exists", side_effect=image_exists),
                        mock.patch.object(self.pipeline, "docker_login") as login,
                        mock.patch.object(self.pipeline, "command") as command,
                        mock.patch.object(self.pipeline, "hook", side_effect=hook) as project_hook,
                        mock.patch.object(self.pipeline, "output"),
                    ):
                        self.pipeline.release(plan, is_prerelease=False)
                    missing = len(plan["images"]) - existing
                    self.assertEqual(login.call_count, int(missing > 0))
                    self.assertEqual(sum(call.args[0] == "publish"
                                         for call in project_hook.call_args_list), missing)
                    self.assertEqual(sum(call.args[:3] == ("gh", "release", "create")
                                         for call in command.call_args_list), int(not complete))

    def test_n8n_quality_example_runs_without_legacy_scripts_or_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            scripts = workdir / ".github/scripts"
            scripts.mkdir(parents=True)
            hook = scripts / "docker-image-project.sh"
            hook.write_text((self.destination / "docs/examples/docker-project-n8n.sh").read_text())
            if shellcheck_path := shutil.which("shellcheck"):
                checked = subprocess.run([shellcheck_path, str(hook)], check=False,
                                         text=True, capture_output=True)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            (workdir / "version").write_text("1.2.3\n")
            tools = workdir / "bin"
            tools.mkdir()
            shellcheck = tools / "shellcheck"
            shellcheck.write_text('#!/bin/bash\nset -eu\nfor script in "$@"; do test -f "$script"; done\n')
            docker = tools / "docker"
            docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$DOCKER_COMMANDS"\n')
            for tool in (shellcheck, docker):
                tool.chmod(0o755)
            log = workdir / "docker-commands"
            result = subprocess.run(
                ["bash", str(hook), "quality"], cwd=workdir,
                env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}",
                     "DOCKER_COMMANDS": str(log)},
                check=False, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = log.read_text().splitlines()
            self.assertEqual(len(commands), 3)
            self.assertTrue(commands[0].startswith("buildx build --check "))
            self.assertTrue(commands[1].startswith("build --build-arg N8N_VERSION=1.2.3 "))
            self.assertTrue(commands[2].startswith("run --rm --entrypoint sh "))

    def test_release_titles_use_exact_tags_when_creating_or_resuming(self) -> None:
        commit = "a" * 40
        for tag, prerelease in [("1.2.3", False), ("v1.2.3", False),
                                ("1.2.3-r1", False), ("1.2.3-rc.1", True),
                                ("1.2.3+build-x", False), ("1.2.3-rc.1+build-x", True)]:
            for tag_commit in (commit,):
                with self.subTest(tag=tag, tag_exists=tag_commit is not None):
                    plan = {
                        **deepcopy(SINGLE),
                        "release_tag": tag,
                        "release_title": "Legacy custom title",
                        "images": [{"name": "extended", "tag": tag.replace("+", "_")}],
                        "image_repository": "mizucopo/example",
                    }
                    with (
                        mock.patch.dict(os.environ, {
                            "GITHUB_REF": "refs/heads/main",
                            "GITHUB_EVENT_NAME": "workflow_dispatch",
                            "RELEASE_SHA": commit,
                            "GIT_USER_NAME": "release",
                            "GIT_USER_EMAIL": "release@example.com",
                        }),
                        mock.patch.object(self.pipeline, "local_tag_commit",
                                          return_value=tag_commit),
                        mock.patch.object(self.pipeline, "github_release_exists",
                                          return_value=False),
                        mock.patch.object(self.pipeline, "hub_token", return_value="token"),
                        mock.patch.object(self.pipeline, "image_exists", return_value=True),
                        mock.patch.object(self.pipeline, "command") as command,
                        mock.patch.object(self.pipeline, "hook", return_value="notes"),
                        mock.patch.object(self.pipeline, "output") as output,
                    ):
                        self.pipeline.release(plan, is_prerelease=prerelease)
                    releases = [call.args for call in command.call_args_list
                                if call.args[:3] == ("gh", "release", "create")]
                    self.assertEqual(len(releases), 1)
                    args = releases[0]
                    self.assertEqual(args[3], tag)
                    self.assertEqual(args[args.index("--title") + 1], tag)
                    self.assertIn("--latest=false", args)
                    self.assertEqual("--prerelease" in args, prerelease)
                    output.assert_called_with("promote_latest", str(not prerelease).lower())
                    with mock.patch.object(self.pipeline, "repository", return_value="mizucopo/example"):
                        self.pipeline.validate_plan({key: plan[key] for key in SINGLE})

    def test_complete_release_is_not_rebuilt(self) -> None:
        plan = {**deepcopy(SINGLE), "image_repository": "mizucopo/n8n-extended"}
        with (
            mock.patch.dict(os.environ, {
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "RELEASE_SHA": "a" * 40,
            }),
            mock.patch.object(self.pipeline, "local_tag_commit", return_value="a" * 40),
            mock.patch.object(self.pipeline, "github_release_exists", return_value=True),
            mock.patch.object(self.pipeline, "hub_token", return_value="token"),
            mock.patch.object(self.pipeline, "image_exists", return_value=True),
            mock.patch.object(self.pipeline, "docker_login") as login,
            mock.patch.object(self.pipeline, "command") as command,
            mock.patch.object(self.pipeline, "hook") as hook,
            mock.patch.object(self.pipeline, "output") as output,
        ):
            self.pipeline.release(plan, is_prerelease=False)
            output.assert_called_with("promote_latest", "true")
            self.pipeline.release(plan, is_prerelease=True)
        login.assert_not_called()
        hook.assert_not_called()
        self.assertFalse(any(call.args[:2] == ("git", "tag")
                             for call in command.call_args_list))
        self.assertFalse(any(call.args[:2] == ("gh", "release")
                             for call in command.call_args_list))
        output.assert_called_with("promote_latest", "false")

    def test_missing_image_and_invalid_digest_fail_closed(self) -> None:
        plan = {**deepcopy(MULTI), "image_repository": "mizucopo/prefect-worker"}
        with (
            mock.patch.dict(os.environ, {
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "RELEASE_SHA": "a" * 40,
            }),
            mock.patch.object(self.pipeline, "local_tag_commit", return_value="a" * 40),
            mock.patch.object(self.pipeline, "github_release_exists", return_value=True),
            mock.patch.object(self.pipeline, "hub_token", return_value="token"),
            mock.patch.object(self.pipeline, "image_exists", side_effect=[True, False]),
        ):
            with self.assertRaisesRegex(self.pipeline.PipelineError, "missing images"):
                self.pipeline.release(plan, is_prerelease=False)
        with mock.patch.object(
            self.pipeline, "api_json", return_value=(200, {"digest": "invalid"})
        ):
            with self.assertRaisesRegex(self.pipeline.PipelineError, "invalid digest"):
                self.pipeline.image_exists(plan, "3.4.5-base-r2", "token")
        with mock.patch.object(
            self.pipeline.request, "urlopen",
            side_effect=error.HTTPError("https://hub.docker.com", 403, "Forbidden", {}, None),
        ):
            with self.assertRaisesRegex(self.pipeline.PipelineError, "HTTP 403"):
                self.pipeline.image_exists(plan, "3.4.5-base-r2")

    def test_quality_gate_runs_project_hook(self) -> None:
        hook = self.destination / ".github/scripts/docker-image-project.sh"
        marker = self.destination / "quality-ran"
        hook.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "case \"$1\" in\n"
            "  quality) test -f \"$DOCKER_RELEASE_PLAN\" && touch \"$QUALITY_MARKER\" ;;\n"
            "  *) exit 2 ;;\n"
            "esac\n"
        )
        subprocess.run(["git", "init", "-q", str(self.destination)], check=True)
        subprocess.run(["git", "-C", str(self.destination), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.destination), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture"], check=True)
        result = subprocess.run(
            ["python3", ".github/scripts/docker-image-pipeline.py", "quality"],
            cwd=self.destination,
            env={**os.environ, "IMAGE_REPOSITORY": "mizucopo/prefect-worker",
                 "QUALITY_MARKER": str(marker)},
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.is_file())


if __name__ == "__main__":
    unittest.main()
