import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urljoin

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

    def test_publication_projects_are_already_formatted(self):
        chrome = (
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
        )
        tauri = ("use_tauri=true", "use_gh_actions_tauri_build=true")
        docker = (
            "use_tauri=true",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
        )
        cases = [
            chrome,
            chrome + (
                "chrome_extension_release_package_root_directory=extensions/browser/app/",
            ),
            chrome + ("chrome_extension_release_package_root_directory=" + "x" * 80,),
            tauri,
            docker,
            docker + ("use_aws_ecr=true",),
            ("use_chrome_extension=true", "use_gh_actions_release=true"),
            ("use_tauri=true", "use_gh_actions_release=true"),
            (
                "use_chrome_extension=true", "use_docker=true",
                "use_gh_actions_docker_release=true",
            ),
            (
                "use_chrome_extension=true", "use_docker=true",
                "use_gh_actions_docker_project_pipeline=true",
            ),
            (
                "use_tauri=true", "use_docker=true",
                "use_gh_actions_docker_project_pipeline=true",
            ),
        ]
        with tempfile.TemporaryDirectory() as tools_directory:
            tools = Path(tools_directory)
            prettier_cli = tools / "node_modules/.bin/prettier"
            env = {
                **os.environ,
                "npm_config_cache": str(test_template.NPM_CACHE),
                "PATH": str(prettier_cli.parent) + os.pathsep + os.environ["PATH"],
            }
            for answers in cases:
                with self.subTest(answers=answers):
                    root = self.render(*answers)
                    package = json.loads((root / "package.json").read_text())
                    prettier = package["devDependencies"]["prettier"]
                    if not prettier_cli.exists():
                        installed = subprocess.run(
                            [
                                "npm", "install", "--no-audit", "--no-fund",
                                "--ignore-scripts", "--prefix", str(tools),
                                "prettier@" + prettier,
                            ],
                            capture_output=True,
                            text=True,
                            env=env,
                        )
                        self.assertEqual(
                            installed.returncode, 0, installed.stdout + installed.stderr
                        )
                    checked_paths = ["CONTRIBUTING.md", "docs/release.md", ".github/release.json"]
                    if "use_gh_actions_docker_project_pipeline=true" in answers:
                        checked_paths.append(".github/workflows/docker-project-quality-checks.yml")
                    for path in checked_paths:
                        self.assertTrue((root / path).is_file(), path)
                        info = subprocess.run(
                            [str(prettier_cli), "--file-info", path],
                            cwd=root,
                            capture_output=True,
                            text=True,
                            env=env,
                        )
                        self.assertEqual(info.returncode, 0, info.stdout + info.stderr)
                        self.assertFalse(json.loads(info.stdout)["ignored"], path)
                    self.assertEqual(package["scripts"]["format:check"], "prettier --check .")
                    checked = subprocess.run(
                        ["npm", "run", "format:check"],
                        cwd=root,
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_release_paths_follow_runtime_sources_and_publication(self):
        runtimes = [
            ((), ["version"]),
            *[
                (
                    ("use_python=true", f"python_project_kind={kind}"),
                    ["pyproject.toml"],
                )
                for kind in ("application", "package", "library")
            ],
            (("use_rust=true",), ["Cargo.toml"]),
            (
                ("use_chrome_extension=true",),
                ["package.json", "src/manifest.json"],
            ),
            (
                ("use_tauri=true",),
                ["package.json", "src-tauri/tauri.conf.json", "src-tauri/Cargo.toml"],
            ),
        ]
        publications = [
            (("use_gh_actions_release=true",), []),
            (
                ("use_docker=true", "use_gh_actions_docker_release=true"),
                ["Dockerfile"],
            ),
            (
                ("use_docker=true", "use_gh_actions_docker_project_pipeline=true"),
                [],
            ),
        ]
        for runtime, sources in runtimes:
            for publication, build_inputs in publications:
                with self.subTest(runtime=runtime, publication=publication):
                    root = self.render(*runtime, *publication)
                    policy = json.loads((root / ".github/release.json").read_text())
                    self.assertEqual(
                        [source["path"] for source in policy["version"]["sources"]],
                        sources,
                    )
                    self.assertEqual(
                        policy["publication"]["release_paths"], sources + build_inputs
                    )
                    for source in sources:
                        self.assertTrue((root / source).is_file(), source)
                    self.assertNotIn("revision_path", policy["version"])
                    self.assertFalse((root / "revision").exists())
                    if build_inputs:
                        # The destination owns the Dockerfile required by this workflow.
                        workflow = (root / ".github/workflows/docker-release.yml").read_text()
                        self.assertIn("docker build --check .", workflow)

    def test_release_paths_follow_runtime_source_precedence(self):
        cases = [
            (
                ("use_python=true", "use_rust=true"),
                ["pyproject.toml"],
            ),
            (
                ("use_python=true", "use_rust=true", "use_chrome_extension=true"),
                ["package.json", "src/manifest.json"],
            ),
            (
                ("use_python=true", "use_tauri=true"),
                ["package.json", "src-tauri/tauri.conf.json", "src-tauri/Cargo.toml"],
            ),
        ]
        for answers, sources in cases:
            with self.subTest(answers=answers):
                root = self.render(*answers, "use_gh_actions_release=true")
                policy = json.loads((root / ".github/release.json").read_text())
                self.assertEqual(policy["publication"]["release_paths"], sources)
                for source in sources:
                    self.assertTrue((root / source).is_file(), source)

    def test_distribution_release_paths_follow_runtime_and_package_root(self):
        for package_root in (".", "extensions/browser\\app/"):
            with self.subTest(package_root=package_root):
                root = self.render(
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                    f"chrome_extension_release_package_root_directory={package_root}",
                )
                prefix = "" if package_root == "." else "extensions/browser/app/"
                policy = json.loads((root / ".github/release.json").read_text())
                self.assertEqual(
                    policy["publication"]["release_paths"],
                    [prefix + "package.json", prefix + "src/manifest.json"],
                )
        root = self.render("use_tauri=true", "use_gh_actions_tauri_build=true")
        policy = json.loads((root / ".github/release.json").read_text())
        self.assertEqual(
            policy["publication"]["release_paths"],
            ["package.json", "src-tauri/tauri.conf.json", "src-tauri/Cargo.toml"],
        )

    def test_release_source_and_path_fields_match_project_formatter(self):
        def value_text(document, field):
            match = re.search(rf'"{field}":\s*', document)
            self.assertIsNotNone(match, field)
            start = match.end()
            _, length = json.JSONDecoder().raw_decode(document[start:])
            return document[start:start + length]

        cases = [
            ("use_chrome_extension=true", "use_gh_actions_chrome_extension_release=true"),
            (
                "use_chrome_extension=true",
                "use_gh_actions_chrome_extension_release=true",
                "chrome_extension_release_package_root_directory=extensions/browser\\app/",
            ),
            ("use_tauri=true", "use_gh_actions_tauri_build=true"),
            (
                "use_tauri=true", "use_docker=true",
                "use_gh_actions_docker_release=true",
            ),
        ]
        for answers in cases:
            with self.subTest(answers=answers):
                root = self.render(*answers)
                package = json.loads((root / "package.json").read_text())
                result = subprocess.run(
                    [
                        "npm", "exec", "--yes", "--package",
                        "prettier@" + package["devDependencies"]["prettier"],
                        "--", "prettier", ".github/release.json",
                    ],
                    cwd=root,
                    env={**os.environ, "npm_config_cache": str(test_template.NPM_CACHE)},
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                original = (root / ".github/release.json").read_text()
                # Other publication formatting is tracked in #162.
                for field in ("sources", "release_paths"):
                    self.assertEqual(
                        value_text(original, field), value_text(result.stdout, field),
                        field,
                    )

    def test_contribution_policy_is_generated_and_all_reader_links_resolve(self):
        cases = [
            ("use_gh_actions_release=true",),
            ("use_docker=true", "use_gh_actions_docker_release=true"),
            ("use_docker=true", "use_gh_actions_docker_project_pipeline=true"),
            (
                "use_chrome_extension=true",
                "use_gh_actions_chrome_extension_release=true",
            ),
            ("use_tauri=true", "use_gh_actions_tauri_build=true"),
            (
                "use_tauri=true", "use_gh_actions_tauri_build=true",
                "use_gh_actions_tauri_homebrew_notify=true",
                "homebrew_tap_repository=owner/homebrew-app",
            ),
            (),
            ("use_version_management=false",),
        ]
        for answers in cases:
            with self.subTest(answers=answers):
                root = self.render(*answers)
                guide = root / "CONTRIBUTING.md"
                self.assertTrue(guide.is_file())
                publication = any(
                    "release=true" in answer
                    or "pipeline=true" in answer
                    or "tauri_build=true" in answer
                    for answer in answers
                )
                readers = [root / "AGENTS.md", root / ".github/pull_request_template.md"]
                if "use_gh_actions_tauri_homebrew_notify=true" in answers:
                    readers.append(root / "docs/homebrew-tap-notification.md")
                if publication:
                    readers.append(root / "docs/release.md")
                    for level in ("patch", "minor", "major"):
                        self.assertIn(f"`release:{level}`", guide.read_text())
                else:
                    self.assertNotIn("`release:patch`", guide.read_text())
                    self.assertIn(
                        "このテンプレートの回答では公開 workflow を生成しません。",
                        guide.read_text(),
                    )
                    self.assertNotIn(
                        "このプロジェクトには公開 workflow がないため",
                        guide.read_text(),
                    )
                for reader in readers:
                    links = re.findall(r"\]\(([^)]+)\)", reader.read_text())
                    policy_links = [link for link in links if "CONTRIBUTING.md" in link]
                    self.assertTrue(policy_links, reader)
                    for link in policy_links:
                        path, _, fragment = link.partition("#")
                        if reader.name == "pull_request_template.md":
                            self.assertEqual(
                                urljoin("https://github.com/owner/project/pull/123", path),
                                "https://github.com/owner/project/blob/main/CONTRIBUTING.md",
                            )
                        else:
                            self.assertEqual(
                                (reader.parent / path).resolve(), guide.resolve()
                            )
                        if fragment:
                            self.assertIn("## " + unquote(fragment), guide.read_text())
                for document in root.rglob("*.md"):
                    if document != guide:
                        self.assertNotRegex(
                            document.read_text(), r"release:(?:patch|minor|major)", document
                        )

    def test_copier_update_preserves_project_owned_release_policy(self):
        current_guide = (test_template.REPO_ROOT / "CONTRIBUTING.md.jinja").read_text()
        previous_guide = (
            current_guide.split("{% else %}", 1)[0]
            + "{% else %}\n"
            + "このプロジェクトには公開 workflow がないため、PR のリリース分類は不要です。\n"
            + "{% endif -%}\n"
        )
        custom_workflow = """name: Project-owned release
on:
  workflow_dispatch:
    inputs:
      upstream_n8n_version:
        required: true
      extended_image_revision:
        required: true
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - run: ./scripts/project-release.sh
"""
        custom_policy = (
            "# 独自リリース方針\n\n"
            "タグは Upstream n8n Version と Extended Image Revision から決定します。\n"
            "release:* ラベルによる汎用採番は利用しません。\n"
        )
        custom_guidance = (
            "\n## 独自リリース\n\n"
            "[独自リリース方針](docs/project-release.md) に従って公開してください。\n"
        )
        for version_management in (False, True):
            with self.subTest(version_management=version_management):
                template, template_guide = self.helper.create_versioned_template(
                    "CONTRIBUTING.md.jinja", previous_guide
                )
                project = self.helper.create_versioned_project(
                    template,
                    "use_python=false",
                    "use_docker=true",
                    f"use_version_management={str(version_management).lower()}",
                )
                workflow = project / ".github/workflows/release-n8n-extended.yml"
                workflow.write_text(custom_workflow)
                policy = project / "docs/project-release.md"
                policy.write_text(custom_policy)
                guide = project / "CONTRIBUTING.md"
                guide.write_text(
                    guide.read_text().replace(
                        "# コントリビューション\n",
                        "# コントリビューション\n" + custom_guidance,
                        1,
                    )
                )
                self.helper.commit_repository(project, "project-owned release policy")

                template_guide.write_text(current_guide)
                self.helper.commit_repository(template, "update contribution guidance")
                updated = self.helper.update_versioned_project(project)

                self.assertEqual(updated.returncode, 0, updated.stdout)
                self.assertEqual(workflow.read_text(), custom_workflow)
                self.assertEqual(policy.read_text(), custom_policy)
                updated_guide = guide.read_text()
                self.assertIn(custom_guidance, updated_guide)
                self.assertNotIn("<<<<<<<", updated_guide)
                self.assertIn(
                    "このテンプレートの回答では公開 workflow を生成しません。",
                    updated_guide,
                )
                self.assertNotIn(
                    "このプロジェクトには公開 workflow がないため", updated_guide
                )
                self.assertIn(
                    "独自の公開 workflow がある場合は、プロジェクトのリリース方針に従ってください。",
                    updated_guide,
                )
                self.assertIn("CONTRIBUTING.md", (project / "AGENTS.md").read_text())
                self.assertFalse((project / ".github/release.json").exists())
                self.assertFalse(
                    (project / ".github/workflows/release-classification.yml").exists()
                )
                answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
                self.assertEqual(answers["use_version_management"], version_management)
                for option, enabled in answers.items():
                    if option.startswith("use_gh_actions_") and "release" in option:
                        self.assertFalse(enabled, option)
                self.assertFalse(answers.get("use_gh_actions_docker_project_pipeline"))

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

    def test_ecr_authentication_and_numbering_are_skipped_without_publication(self):
        for python in ["true", "false"]:
            with self.subTest(python=python):
                name, option = "docker-release.yml", "use_gh_actions_docker_release=true"
                root = self.render(f"use_python={python}", "use_docker=true", "use_aws_ecr=true", option)
                workflow = yaml.safe_load((root / ".github/workflows" / name).read_text())
                prepare = workflow["jobs"]["prepare"]
                steps = prepare["steps"]
                intent = next(step for step in steps if step.get("id") == "intent")
                auth = next(step for step in steps if "configure-aws-credentials@" in step.get("uses", ""))
                number = next(step for step in steps if step.get("id") == "prepare")
                self.assertLess(steps.index(intent), steps.index(auth))
                self.assertLess(steps.index(auth), steps.index(number))
                self.assertEqual(intent["run"], "python3 -I .github/scripts/release.py intent")
                self.assertNotIn("AWS_ROLE_ARN", str(intent))
                self.assertNotIn("if", intent)
                self.assertEqual(auth["if"], "${{ steps.intent.outputs.publish == 'true' }}")
                self.assertEqual(number["if"], auth["if"])
                self.assertEqual(prepare["outputs"]["publish"], "${{ steps.prepare.outputs.publish || 'false' }}")
                for job in workflow["jobs"].values():
                    if job is not prepare and "prepare" in job.get("needs", []):
                        if "if" in job:
                            self.assertIn("needs.prepare.outputs.publish == 'true'", job["if"])
                        else:
                            self.assertIn("release", job["needs"])

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
