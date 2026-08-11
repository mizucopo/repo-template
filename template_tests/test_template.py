import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
NPM_CACHE = Path(tempfile.gettempdir()) / "repo-template-npm-cache"


class TemplateTest(unittest.TestCase):
    def copy_template_into(
        self,
        destination: Path,
        *answers: str,
        overwrite: bool = False,
        pretend: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = ["copier", "copy", "--trust", "--defaults"]
        if overwrite:
            command.append("--overwrite")
        if pretend:
            command.append("--pretend")
        for answer in answers:
            command.extend(["-d", answer])
        command.extend([str(REPO_ROOT), str(destination)])

        return subprocess.run(
            command,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def recopy_template(self, destination: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["copier", "recopy", "--trust", "-f", str(destination)],
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def copy_template(self, *answers: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        destination_root = tempfile.TemporaryDirectory()
        self.addCleanup(destination_root.cleanup)

        destination = Path(destination_root.name) / "project"
        result = self.copy_template_into(destination, *answers)
        return result, destination

    @staticmethod
    def expected_dependabot_config(*updates: tuple[str, str]) -> str:
        lines = ["version: 2", "updates:"]
        for ecosystem, directory in updates:
            lines.extend(
                [
                    f'  - package-ecosystem: "{ecosystem}"',
                    f'    directory: "{directory}"',
                    "    schedule:",
                    '      interval: "weekly"',
                    "    groups:",
                ]
            )
            if ecosystem == "github-actions":
                lines.extend(
                    [
                        "      github-actions:",
                        "        patterns:",
                        '          - "*"',
                    ]
                )
            else:
                lines.extend(
                    [
                        "      minor-and-patch:",
                        "        update-types:",
                        '          - "minor"',
                        '          - "patch"',
                    ]
                )
        return "\n".join(lines) + "\n"

    def test_docker_build_context_policy_is_generated_only_for_docker(self) -> None:
        expected_policy = """# Exclude every build input unless the project explicitly allows it.
**
!Dockerfile
"""
        configurations = {
            "default": ((), False),
            "docker": (("use_python=false", "use_docker=true"), True),
            "docker_release": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                True,
            ),
        }

        for name, (answers, expected) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                dockerignore = destination / ".dockerignore"
                self.assertEqual(dockerignore.exists(), expected)
                if expected:
                    self.assertEqual(dockerignore.read_text(), expected_policy)

    def test_existing_dockerignore_requires_explicit_template_ownership(self) -> None:
        destination_root = tempfile.TemporaryDirectory()
        self.addCleanup(destination_root.cleanup)
        destination = Path(destination_root.name) / "existing-project"
        destination.mkdir()
        dockerignore = destination / ".dockerignore"
        dockerignore.write_text("videos/\n.env\n")

        preview = self.copy_template_into(
            destination,
            "use_python=false",
            "use_docker=true",
            overwrite=True,
            pretend=True,
        )

        self.assertEqual(preview.returncode, 0, preview.stdout)
        self.assertEqual(dockerignore.read_text(), "videos/\n.env\n")

        result = self.copy_template_into(
            destination,
            "use_python=false",
            "use_docker=true",
            overwrite=True,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            dockerignore.read_text(),
            """# Exclude every build input unless the project explicitly allows it.
**
!Dockerfile
""",
        )

    def test_readme_documents_docker_build_context_policy(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text()

        for expected_guidance in (
            "Docker build contextを安全に保つ",
            "strict allowlist",
            "!src/**",
            "--pretend --overwrite",
            "project固有のallowlist追加とテンプレート更新のmerge結果",
            "copier recopy -f",
        ):
            with self.subTest(guidance=expected_guidance):
                self.assertIn(expected_guidance, readme)

    def test_python_application_is_not_installed_as_a_package(self) -> None:
        result, destination = self.copy_template("use_python=true")

        self.assertEqual(result.returncode, 0, result.stdout)
        pyproject = (destination / "pyproject.toml").read_text()
        self.assertIn("[tool.uv]\npackage = false", pyproject)
        self.assertEqual((destination / "src/__init__.py").read_text(), "")

    def test_project_metadata_and_python_project_kinds_are_rendered(self) -> None:
        cases = {
            "application": ("application", "src/__init__.py", "package = false"),
            "package": ("package", "src/sample_project/__init__.py", "package = true"),
            "library": ("library", "src/sample_project/__init__.py", "package = true"),
        }

        for name, (kind, source_path, package_setting) in cases.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(
                    "use_python=true",
                    "project_name=sample-project",
                    "project_description=Sample project",
                    "project_version=1.2.3",
                    f"python_project_kind={kind}",
                    "python_package_name=sample_project",
                )
                self.assertEqual(result.returncode, 0, result.stdout)

                pyproject = (destination / "pyproject.toml").read_text()
                self.assertIn('name = "sample-project"', pyproject)
                self.assertIn('version = "1.2.3"', pyproject)
                self.assertIn('description = "Sample project"', pyproject)
                self.assertIn(package_setting, pyproject)
                self.assertTrue((destination / source_path).is_file())

                if kind == "application":
                    self.assertNotIn("[build-system]", pyproject)
                else:
                    self.assertIn("[build-system]", pyproject)
                    self.assertIn('packages = ["src/sample_project"]', pyproject)

    def test_python_tasks_separate_checks_from_fixes(self) -> None:
        result, destination = self.copy_template("use_python=true")

        self.assertEqual(result.returncode, 0, result.stdout)
        pyproject = (destination / "pyproject.toml").read_text()
        self.assertIn(
            'check = "ruff check src tests stubs && ruff format --check '
            'src tests stubs && mypy && pytest"',
            pyproject,
        )
        self.assertIn(
            'fix = "ruff check --fix src tests stubs && ruff format src tests stubs"',
            pyproject,
        )
        self.assertIn('test = "task check"', pyproject)

    def test_python_package_initializer_is_empty(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "python_project_kind=library",
            "project_name=sample-library",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        source = (destination / "src/sample_library/__init__.py").read_text()
        self.assertEqual(source, "")

    def test_python_package_name_rejects_keywords(self) -> None:
        for package_name in ("class", "import", "async"):
            with self.subTest(package_name=package_name):
                result, _destination = self.copy_template(
                    "use_python=true",
                    "python_project_kind=library",
                    f"project_name={package_name}",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Python予約語", result.stdout)

    def test_starter_source_is_preserved_on_recopy(self) -> None:
        result, destination = self.copy_template("use_python=true")
        self.assertEqual(result.returncode, 0, result.stdout)

        starter = destination / "src/__init__.py"
        starter.write_text('"""Project-owned implementation."""\n')

        recopy = self.recopy_template(destination)

        self.assertEqual(recopy.returncode, 0, recopy.stdout)
        self.assertEqual(
            starter.read_text(),
            '"""Project-owned implementation."""\n',
        )

    def test_deleted_starter_source_remains_deleted_on_recopy(self) -> None:
        cases = {
            "python_application": (("use_python=true",), "src/__init__.py"),
            "python_library": (
                (
                    "use_python=true",
                    "project_name=sample-library",
                    "python_project_kind=library",
                ),
                "src/sample_library/__init__.py",
            ),
            "rust": (("use_python=false", "use_rust=true"), "src/main.rs"),
            "chrome": (
                ("use_python=false", "use_chrome_extension=true"),
                "src/background.ts",
            ),
            "tauri": (("use_python=false", "use_tauri=true"), "index.html"),
        }

        for name, (answers, starter_path) in cases.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                starter = destination / starter_path
                starter.unlink()

                recopy = self.recopy_template(destination)

                self.assertEqual(recopy.returncode, 0, recopy.stdout)
                self.assertFalse(starter.exists())

    def test_newly_enabled_runtime_generates_its_starter_source(self) -> None:
        cases = {
            "python": (
                ("use_python=false",),
                "use_python: false",
                "use_python: true",
                "src/__init__.py",
            ),
            "rust": (
                ("use_python=false",),
                "use_rust: false",
                "use_rust: true",
                "src/main.rs",
            ),
            "chrome": (
                ("use_python=false",),
                "use_chrome_extension: false",
                "use_chrome_extension: true",
                "src/background.ts",
            ),
            "tauri": (
                ("use_python=false",),
                "use_tauri: false",
                "use_tauri: true",
                "index.html",
            ),
            "python_library": (
                ("use_python=true",),
                "python_project_kind: application",
                "python_project_kind: library",
                "src/test_project/__init__.py",
            ),
        }

        for name, (
            initial_answers,
            old_answer,
            new_answer,
            starter_path,
        ) in cases.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*initial_answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                answers_path = destination / ".copier-answers.yml"
                answers = answers_path.read_text()
                self.assertIn(old_answer, answers)
                answers_path.write_text(answers.replace(old_answer, new_answer))

                recopy = self.recopy_template(destination)

                self.assertEqual(recopy.returncode, 0, recopy.stdout)
                self.assertTrue((destination / starter_path).is_file())

    def test_disabled_runtime_keeps_its_starter_history(self) -> None:
        cases = {
            "python": (
                ("use_python=true",),
                "use_python: true",
                "use_python: false",
                "src/__init__.py",
            ),
            "python_library": (
                ("use_python=true", "python_project_kind=library"),
                "python_project_kind: library",
                "python_project_kind: application",
                "src/test_project/__init__.py",
            ),
            "rust": (
                ("use_python=false", "use_rust=true"),
                "use_rust: true",
                "use_rust: false",
                "src/main.rs",
            ),
            "chrome": (
                ("use_python=false", "use_chrome_extension=true"),
                "use_chrome_extension: true",
                "use_chrome_extension: false",
                "src/background.ts",
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                "use_tauri: true",
                "use_tauri: false",
                "index.html",
            ),
        }

        for name, (initial_answers, enabled, disabled, starter_path) in cases.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*initial_answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                starter = destination / starter_path
                starter.unlink()
                answers_path = destination / ".copier-answers.yml"

                answers = answers_path.read_text()
                self.assertIn(enabled, answers)
                answers_path.write_text(answers.replace(enabled, disabled))
                disabled_recopy = self.recopy_template(destination)
                self.assertEqual(disabled_recopy.returncode, 0, disabled_recopy.stdout)

                answers = answers_path.read_text()
                self.assertIn(disabled, answers)
                answers_path.write_text(answers.replace(disabled, enabled))
                enabled_recopy = self.recopy_template(destination)

                self.assertEqual(enabled_recopy.returncode, 0, enabled_recopy.stdout)
                self.assertFalse(starter.exists())

    def test_docker_quality_workflow_is_opt_in(self) -> None:
        disabled, disabled_destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
        )
        self.assertEqual(disabled.returncode, 0, disabled.stdout)
        self.assertFalse(
            (
                disabled_destination
                / ".github/workflows/docker-quality-checks.yml"
            ).exists()
        )

        enabled, enabled_destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
            "use_gh_actions_docker_quality=true",
            "dockerfile_path=docker\\app.Dockerfile",
            "docker_build_context=docker\\app",
            "docker_smoke_command=python --version",
        )
        self.assertEqual(enabled.returncode, 0, enabled.stdout)
        workflow = (
            enabled_destination / ".github/workflows/docker-quality-checks.yml"
        ).read_text()
        self.assertIn("  docker-quality-checks:", workflow)
        self.assertIn("docker buildx build --check", workflow)
        self.assertIn('DOCKERFILE_PATH: "docker/app.Dockerfile"', workflow)
        self.assertIn('DOCKER_BUILD_CONTEXT: "docker/app"', workflow)
        self.assertIn('DOCKER_SMOKE_COMMAND: "python --version"', workflow)
        self.assertIn("docker/build-push-action@", workflow)
        self.assertIn("docker run --rm --entrypoint sh", workflow)

    def test_readme_documents_python_docker_application_layout(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text()

        for expected_guidance in (
            "src-root application layout",
            "package = false",
            "package = true",
            "再利用ライブラリ",
            "--no-install-project",
        ):
            with self.subTest(guidance=expected_guidance):
                self.assertIn(expected_guidance, readme)

    def test_agent_workflow_guidance_and_docs_are_generated(self) -> None:
        configurations = {
            "default": (),
            "no_runtime": ("use_python=false",),
            "rust": ("use_python=false", "use_rust=true"),
            "tauri": ("use_python=false", "use_tauri=true"),
            "chrome": (
                "use_python=false",
                "use_chrome_extension=true",
            ),
        }
        required_rules = (
            "Never make changes directly on `main`.",
            "Before starting work, create a GitHub Issue that describes the work.",
            "Perform the work on a non-`main` branch associated with that Issue.",
        )
        linked_docs = {
            "docs/agents/issue-tracker.md": (
                "GitHub Issues",
                "configured Git remote",
            ),
            "docs/agents/triage-labels.md": (
                "needs-triage",
                "needs-info",
                "ready-for-agent",
                "ready-for-human",
                "wontfix",
            ),
            "docs/agents/domain.md": (
                "single-context",
                "`CONTEXT.md`",
                "`docs/adr/`",
            ),
        }

        for name, answers in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                agents_guidance = (destination / "AGENTS.md").read_text()
                claude_guidance = (destination / "CLAUDE.md").read_text()
                self.assertEqual(agents_guidance, claude_guidance)
                self.assertIn("## Agent skills", agents_guidance)
                for rule in required_rules:
                    self.assertIn(rule, agents_guidance)

                for relative_path, required_content in linked_docs.items():
                    self.assertIn(f"`{relative_path}`", agents_guidance)
                    generated_doc = destination / relative_path
                    self.assertTrue(generated_doc.is_file(), relative_path)
                    content = generated_doc.read_text()
                    for expected in required_content:
                        self.assertIn(expected, content)

    def run_pr_tag_version_reader(
        self, destination: Path
    ) -> subprocess.CompletedProcess[str]:
        return self.run_release_version_reader(destination, "pr-tag-check.yml")

    def run_chrome_release_metadata_reader(
        self, destination: Path
    ) -> subprocess.CompletedProcess[str]:
        workflow = (
            destination / ".github/workflows/chrome-extension-release.yml"
        ).read_text()
        start_marker = "          node <<'NODE'\n"
        end_marker = "\n          NODE"
        start = workflow.index(start_marker) + len(start_marker)
        end = workflow.index(end_marker, start)
        script = "\n".join(
            line.removeprefix("          ")
            for line in workflow[start:end].splitlines()
        )
        output_path = destination / "github-output.txt"
        runner_temp = destination / "runner-temp"
        notes_path = runner_temp / "release-notes.md"
        runner_temp.mkdir(exist_ok=True)
        output_path.unlink(missing_ok=True)
        notes_path.unlink(missing_ok=True)

        return subprocess.run(
            ["node"],
            input=f"{script}\n",
            cwd=destination,
            check=False,
            env={
                **os.environ,
                "GITHUB_OUTPUT": str(output_path),
                "RUNNER_TEMP": str(runner_temp),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def run_release_version_reader(
        self,
        destination: Path,
        workflow_name: str,
    ) -> subprocess.CompletedProcess[str]:
        output_path = destination / "github-output.txt"
        error_path = destination / "version_check_error.txt"
        output_path.unlink(missing_ok=True)
        error_path.unlink(missing_ok=True)

        return self.run_process(
            ["bash"],
            destination,
            env={**os.environ, "GITHUB_OUTPUT": str(output_path)},
            script=self.workflow_step_script(
                destination,
                workflow_name,
                "Read version",
            ),
        )

    @staticmethod
    def write_version_source(
        destination: Path,
        source: str,
        version: str,
    ) -> None:
        if source == "plain":
            (destination / "version").write_text(f"{version}\n")
            return

        if source in {"python", "rust"}:
            filename = "pyproject.toml" if source == "python" else "Cargo.toml"
            path = destination / filename
            lines = path.read_text().splitlines()
            for index, line in enumerate(lines):
                if line.startswith("version = "):
                    lines[index] = f"version = {json.dumps(version)}"
                    path.write_text("\n".join(lines) + "\n")
                    return
            raise AssertionError(f"version source was not found in {filename}")

        package_path = destination / "package.json"
        package = json.loads(package_path.read_text())
        package["version"] = version
        package_path.write_text(json.dumps(package, indent=2) + "\n")

        if source == "chrome":
            manifest_path = destination / "src/manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["version"] = version
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    def run_chrome_release_distribution_manifest_validator(
        self,
        destination: Path,
        *,
        package_root: str,
        distribution_root: str,
        expected_version: str,
    ) -> subprocess.CompletedProcess[str]:
        workflow = (
            destination / ".github/workflows/chrome-extension-release.yml"
        ).read_text()
        step_marker = "      - name: Validate distribution manifest\n"
        start_marker = "          node <<'NODE'\n"
        end_marker = "\n          NODE"
        step_start = workflow.index(step_marker)
        start = workflow.index(start_marker, step_start) + len(start_marker)
        end = workflow.index(end_marker, start)
        script = "\n".join(
            line.removeprefix("          ")
            for line in workflow[start:end].splitlines()
        )
        cwd = destination if package_root == "." else destination / package_root

        return subprocess.run(
            ["node"],
            input=f"{script}\n",
            cwd=cwd,
            check=False,
            env={
                **os.environ,
                "DISTRIBUTION_ROOT": distribution_root,
                "EXPECTED_VERSION": expected_version,
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    @staticmethod
    def workflow_step_script(
        destination: Path,
        workflow_name: str,
        step_name: str,
    ) -> str:
        workflow = (destination / ".github/workflows" / workflow_name).read_text()
        step_marker = f"      - name: {step_name}\n"
        start_marker = "        run: |\n"
        step_start = workflow.index(step_marker)
        start = workflow.index(start_marker, step_start) + len(start_marker)
        end = workflow.find("\n\n      - name:", start)
        if end == -1:
            end = len(workflow)
        return "\n".join(
            line.removeprefix("          ")
            for line in workflow[start:end].splitlines()
        )

    @staticmethod
    def run_process(
        command: list[str],
        destination: Path,
        *,
        env: dict[str, str] | None = None,
        script: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            input=f"{script}\n" if script is not None else None,
            cwd=destination,
            check=False,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def test_chrome_manifest_json_values_are_escaped(self) -> None:
        name = 'Quote " Name \\ Test'
        description = 'Description with "quote" and \\ slash'

        result, destination = self.copy_template(
            "use_chrome_extension=true",
            f"chrome_extension_name={name}",
            f"chrome_extension_description={description}",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        manifest = json.loads((destination / "src/manifest.json").read_text())
        package = json.loads((destination / "package.json").read_text())

        self.assertEqual(manifest["name"], name)
        self.assertEqual(manifest["description"], description)
        self.assertEqual(package["description"], description)

    def test_chrome_package_author_is_escaped(self) -> None:
        author_name = 'Quote " Author \\ Name'

        result, destination = self.copy_template(
            "use_chrome_extension=true",
            f"author_name={author_name}",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        package = json.loads((destination / "package.json").read_text())

        self.assertEqual(package["author"], author_name)

    def test_release_workflows_use_stable_copier_author(self) -> None:
        author_name = 'Quote " Release \\ Author'
        author_email = "release+tag@example.com"
        configurations = {
            "release": (
                (
                    "use_python=false",
                    "use_gh_actions_release=true",
                ),
                "release.yml",
            ),
            "docker_release": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                "docker-release.yml",
            ),
            "chrome_extension_release": (
                (
                    "use_python=false",
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                ),
                "chrome-extension-release.yml",
            ),
        }

        for name, (answers, workflow_name) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(
                    *answers,
                    f"author_name={author_name}",
                    f"author_email={author_email}",
                )
                self.assertEqual(result.returncode, 0, result.stdout)

                workflow = (
                    destination / ".github/workflows" / workflow_name
                ).read_text()
                self.assertIn(
                    f"GIT_USER_NAME: {json.dumps(author_name)}",
                    workflow,
                )
                self.assertIn(
                    f"GIT_USER_EMAIL: {json.dumps(author_email)}",
                    workflow,
                )
                self.assertIn('git config user.name "$GIT_USER_NAME"', workflow)
                self.assertIn('git config user.email "$GIT_USER_EMAIL"', workflow)
                self.assertNotIn("Read git author", workflow)
                self.assertNotIn("git log -1", workflow)
                self.assertNotIn("steps.author.outputs", workflow)

                copier_answers = (destination / ".copier-answers.yml").read_text()
                self.assertIn(author_email, copier_answers)

    def test_generated_release_workflows_pass_git_diff_check(self) -> None:
        configurations = {
            "release": (
                "use_python=false",
                "use_gh_actions_release=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "docker_release": (
                "use_python=false",
                "use_docker=true",
                "use_gh_actions_docker_release=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "chrome_extension_release": (
                "use_python=false",
                "use_chrome_extension=true",
                "use_gh_actions_chrome_extension_release=true",
                "use_gh_actions_pr_tag_check=true",
            ),
        }

        for name, answers in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                for command in (("init",), ("add", ".")):
                    git_result = self.run_process(
                        ["git", *command],
                        destination,
                    )
                    self.assertEqual(git_result.returncode, 0, git_result.stdout)

                check_result = self.run_process(
                    ["git", "diff", "--cached", "--check"],
                    destination,
                )
                self.assertEqual(check_result.returncode, 0, check_result.stdout)

    def test_docker_hub_login_username_is_separate_from_image_namespace(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
            "use_gh_actions_pr_tag_check=true",
            "docker_registry=image-owner",
            "docker_login_username=release-bot",
            "docker_image_name=test-project",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        docker_release = (
            destination / ".github/workflows/docker-release.yml"
        ).read_text()
        pr_tag_check = (
            destination / ".github/workflows/pr-tag-check.yml"
        ).read_text()

        self.assertIn('DOCKERHUB_USERNAME: "release-bot"', docker_release)
        self.assertIn('DOCKERHUB_NAMESPACE: "image-owner"', docker_release)
        self.assertNotIn("DOCKERHUB_USERNAME", pr_tag_check)
        self.assertIn('DOCKERHUB_NAMESPACE: "image-owner"', pr_tag_check)
        self.assertIn('username: "release-bot"', docker_release)
        self.assertIn("image-owner/test-project:latest", docker_release)
        self.assertNotIn("release-bot/test-project", docker_release)

        copier_answers = (destination / ".copier-answers.yml").read_text()
        self.assertIn("docker_registry: image-owner", copier_answers)
        self.assertIn("docker_login_username: release-bot", copier_answers)

    def test_docker_hub_login_username_defaults_to_image_namespace(self) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
            "docker_registry=image-owner",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        docker_release = (
            destination / ".github/workflows/docker-release.yml"
        ).read_text()
        self.assertIn('DOCKERHUB_USERNAME: "image-owner"', docker_release)
        self.assertIn('DOCKERHUB_NAMESPACE: "image-owner"', docker_release)
        self.assertIn('username: "image-owner"', docker_release)

    def test_docker_registry_guidance_distinguishes_docker_hub_and_ecr(
        self,
    ) -> None:
        copier_config = (REPO_ROOT / "copier.yml").read_text()
        readme = (REPO_ROOT / "README.md").read_text()

        self.assertIn(
            'help: "{% if use_aws_ecr %}ECR registry host'
            '{% else %}Docker Hub image namespace{% endif %}"',
            copier_config,
        )
        self.assertIn(
            "Docker Hubではimage namespace、Amazon ECRではregistry host",
            readme,
        )

        registry = "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com"
        result, destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
            "use_aws_ecr=true",
            "aws_account_id=123456789012",
            "aws_region=ap-northeast-1",
            f"docker_registry={registry}",
            "docker_image_name=test-project",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        docker_release = (
            destination / ".github/workflows/docker-release.yml"
        ).read_text()
        self.assertIn(f"{registry}/test-project:latest", docker_release)
        self.assertNotIn("DOCKERHUB_USERNAME", docker_release)

        copier_answers = (destination / ".copier-answers.yml").read_text()
        self.assertIn(f"docker_registry: {registry}", copier_answers)
        self.assertNotIn("docker_login_username", copier_answers)

    def test_release_workflows_classify_partial_release_states(self) -> None:
        configurations = {
            "release": (
                (
                    "use_python=false",
                    "use_gh_actions_release=true",
                ),
                "release.yml",
            ),
            "docker_release": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                "docker-release.yml",
            ),
            "chrome_extension_release": (
                (
                    "use_python=false",
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                ),
                "chrome-extension-release.yml",
            ),
        }

        for name, (answers, workflow_name) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                workflow = (
                    destination / ".github/workflows" / workflow_name
                ).read_text()
                if name == "docker_release":
                    self.assertNotIn("concurrency:", workflow)
                    self.assertIn("  actions: read", workflow)
                    self.assertIn(
                        "      - name: Wait for earlier release runs\n"
                        "        id: release-queue\n",
                        workflow,
                    )
                    self.assertIn("gh api --paginate", workflow)
                    self.assertIn("| jq -s", workflow)
                    self.assertIn('poll_interval=30', workflow)
                    self.assertIn('poll_interval=300', workflow)
                    self.assertIn(
                        "select(.run_number < $current "
                        'and .status != "completed")',
                        workflow,
                    )
                    self.assertIn(
                        "if [ \"$GITHUB_RUN_ATTEMPT\" -gt 1 ]",
                        workflow,
                    )
                    self.assertIn(
                        "select(.run_number > $current "
                        'and .conclusion == "success")',
                        workflow,
                    )
                    self.assertIn(
                        'echo "publish_latest=$publish_latest" '
                        '>> "$GITHUB_OUTPUT"',
                        workflow,
                    )
                else:
                    self.assertIn(
                        "concurrency:\n"
                        "  group: ${{ github.workflow }}-${{ github.sha }}\n"
                        "  cancel-in-progress: false",
                        workflow,
                    )
                if name != "chrome_extension_release":
                    self.assertIn(
                        "      - name: Require main\n"
                        "        run: |\n"
                        "          set -euo pipefail",
                        workflow,
                    )
                self.assertIn(
                    "      - name: Inspect release state\n"
                    "        id: release-state",
                    workflow,
                )
                self.assertFalse((destination / "_release_state_reader.sh").exists())

                origin = destination.parent / "origin.git"
                git_commands = (
                    ("init", "--initial-branch=main"),
                    ("config", "user.name", "Release Test"),
                    ("config", "user.email", "release-test@example.com"),
                    ("add", "."),
                    ("commit", "-m", "Initial release commit"),
                    ("init", "--bare", "--initial-branch=main", str(origin)),
                    ("remote", "add", "origin", str(origin)),
                )
                for command in git_commands:
                    git_result = self.run_process(
                        ["git", *command],
                        destination,
                    )
                    self.assertEqual(git_result.returncode, 0, git_result.stdout)

                fake_bin = destination.parent / "bin"
                fake_bin.mkdir()
                fake_curl = fake_bin / "curl"
                fake_curl.write_text(
                    "#!/bin/sh\n"
                    "output=\n"
                    "while [ \"$#\" -gt 0 ]; do\n"
                    "  case \"$1\" in\n"
                    "    --output) shift; output=$1 ;;\n"
                    "  esac\n"
                    "  shift\n"
                    "done\n"
                    "if [ -n \"$output\" ]; then\n"
                    "  printf '{\"assets\":[{\"name\":\"%s\"}]}' \"${FAKE_ASSET_NAME:-other.zip}\" > \"$output\"\n"
                    "fi\n"
                    "printf '%s' \"${FAKE_HTTP_STATUS:-404}\"\n"
                    "exit \"${FAKE_CURL_EXIT:-0}\"\n"
                )
                fake_curl.chmod(0o755)
                state_script = self.workflow_step_script(
                    destination,
                    workflow_name,
                    "Inspect release state",
                )
                output_path = destination / "github-output.txt"
                state_env = {
                    **os.environ,
                    "FAKE_HTTP_STATUS": "404",
                    "GH_TOKEN": "test-token",
                    "GITHUB_API_URL": "https://api.github.example",
                    "GITHUB_OUTPUT": str(output_path),
                    "GITHUB_REPOSITORY": "owner/project",
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "TAG": "0.1.0",
                }
                if workflow_name == "chrome-extension-release.yml":
                    state_env["RELEASE_ASSET_NAME"] = "chrome-extension-0.1.0.zip"

                missing_result = self.run_process(
                    ["bash"], destination, env=state_env, script=state_script
                )
                self.assertEqual(missing_result.returncode, 0, missing_result.stdout)
                self.assertIn("tag_exists=false", output_path.read_text())
                self.assertIn("release_exists=false", output_path.read_text())

                output_path.unlink()
                release_only_result = self.run_process(
                    ["bash"],
                    destination,
                    env={**state_env, "FAKE_HTTP_STATUS": "200"},
                    script=state_script,
                )
                self.assertNotEqual(release_only_result.returncode, 0)
                self.assertIn("matching git tag was not found", release_only_result.stdout)

                tag_result = self.run_process(
                    ["git", "tag", "-a", "0.1.0", "-m", "Release 0.1.0"],
                    destination,
                )
                self.assertEqual(tag_result.returncode, 0, tag_result.stdout)

                output_path.unlink(missing_ok=True)
                tag_only_result = self.run_process(
                    ["bash"], destination, env=state_env, script=state_script
                )
                self.assertEqual(tag_only_result.returncode, 0, tag_only_result.stdout)
                self.assertIn("tag_exists=true", output_path.read_text())
                self.assertIn("release_exists=false", output_path.read_text())

                output_path.unlink()
                complete_env = {**state_env, "FAKE_HTTP_STATUS": "200"}
                if workflow_name == "chrome-extension-release.yml":
                    complete_env["FAKE_ASSET_NAME"] = state_env["RELEASE_ASSET_NAME"]
                complete_result = self.run_process(
                    ["bash"], destination, env=complete_env, script=state_script
                )
                self.assertEqual(complete_result.returncode, 0, complete_result.stdout)
                state_output = output_path.read_text()
                self.assertIn("tag_exists=true", state_output)
                self.assertIn("release_exists=true", state_output)
                if workflow_name == "chrome-extension-release.yml":
                    self.assertIn("release_asset_exists=true", state_output)

                    output_path.unlink()
                    asset_missing_result = self.run_process(
                        ["bash"],
                        destination,
                        env={**complete_env, "FAKE_ASSET_NAME": "other.zip"},
                        script=state_script,
                    )
                    self.assertEqual(
                        asset_missing_result.returncode,
                        0,
                        asset_missing_result.stdout,
                    )
                    self.assertIn(
                        "release_asset_exists=false", output_path.read_text()
                    )

                output_path.unlink(missing_ok=True)
                api_failure_result = self.run_process(
                    ["bash"],
                    destination,
                    env={**state_env, "FAKE_HTTP_STATUS": "500"},
                    script=state_script,
                )
                self.assertNotEqual(api_failure_result.returncode, 0)
                self.assertIn("HTTP 500", api_failure_result.stdout)

                (destination / "after-release.txt").write_text("next commit\n")
                for command in (
                    ("add", "after-release.txt"),
                    ("commit", "-m", "Move release commit"),
                ):
                    git_result = self.run_process(
                        ["git", *command],
                        destination,
                    )
                    self.assertEqual(git_result.returncode, 0, git_result.stdout)

                foreign_commit_result = self.run_process(
                    ["bash"],
                    destination,
                    env=complete_env,
                    script=state_script,
                )
                self.assertNotEqual(foreign_commit_result.returncode, 0)
                self.assertIn(
                    "already points to",
                    foreign_commit_result.stdout,
                )

    def test_release_workflows_resume_only_missing_work(self) -> None:
        configurations = {
            "release": (
                ("use_python=false", "use_gh_actions_release=true"),
                "release.yml",
            ),
            "docker_hub": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                "docker-release.yml",
            ),
            "ecr": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                    "use_aws_ecr=true",
                ),
                "docker-release.yml",
            ),
            "chrome_extension": (
                (
                    "use_python=false",
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                ),
                "chrome-extension-release.yml",
            ),
        }

        for name, (answers, workflow_name) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                workflow = (
                    destination / ".github/workflows" / workflow_name
                ).read_text()

                self.assertIn(
                    "      - name: Create version tag\n"
                    "        if: steps.release-state.outputs.tag_exists != 'true'",
                    workflow,
                )
                self.assertIn(
                    "      - name: Create GitHub Release\n"
                    "        if: steps.release-state.outputs.release_exists != 'true'",
                    workflow,
                )

                if name == "release":
                    continue

                if name == "chrome_extension":
                    rebuild_condition = (
                        "steps.release-state.outputs.release_asset_exists != 'true'"
                    )
                    for step_name in (
                        "Install dependencies",
                        "Run quality gate",
                        "Build extension",
                        "Resolve distribution root",
                        "Validate distribution manifest",
                        "Create distribution zip",
                    ):
                        self.assertIn(
                            f"      - name: {step_name}\n"
                            f"        if: {rebuild_condition}",
                            workflow,
                        )
                    self.assertIn(
                        "      - name: Upload distribution zip\n"
                        f"        if: {rebuild_condition}",
                        workflow,
                    )
                    self.assertNotIn("--clobber", workflow)
                    continue

                self.assertLess(
                    workflow.index("      - name: Inspect Docker image state"),
                    workflow.index("      - name: Create version tag"),
                )
                self.assertLess(
                    workflow.index("      - name: Create version tag"),
                    workflow.index("      - name: Build and push"),
                )
                self.assertIn(
                    "      - name: Build and push\n"
                    "        if: steps.image-state.outputs.version_exists != 'true'",
                    workflow,
                )
                self.assertIn(
                    "steps.image-state.outputs.latest_matches != 'true'",
                    workflow,
                )
                self.assertNotIn("id: main-tip", workflow)
                self.assertIn(
                    "      - name: Publish latest tag\n"
                    "        if: steps.release-queue.outputs.publish_latest "
                    "== 'true' "
                    "&& steps.image-state.outputs.latest_matches != 'true'",
                    workflow,
                )
                self.assertIn(
                    "PUBLISH_LATEST: "
                    "${{ steps.release-queue.outputs.publish_latest }}",
                    workflow,
                )
                self.assertIn('latest_option="--latest=false"', workflow)
                self.assertIn('latest_option="--latest"', workflow)
                self.assertIn('latest_notes=""', workflow)
                self.assertIn("$latest_notes", workflow)
                if name == "docker_hub":
                    self.assertIn("https://hub.docker.com/v2/auth/token", workflow)
                    self.assertIn("/tags/$TAG", workflow)
                    self.assertIn("docker buildx imagetools create", workflow)
                    self.assertNotIn("steps.dockerhub-auth.outputs.token", workflow)
                    image_state_script = self.workflow_step_script(
                        destination,
                        workflow_name,
                        "Inspect Docker image state",
                    )
                    self.assertIn("::add-mask::$DOCKERHUB_API_TOKEN", image_state_script)
                else:
                    self.assertIn("aws ecr batch-get-image", workflow)
                    self.assertIn("aws ecr put-image", workflow)

    def test_docker_release_classifies_registry_image_states(self) -> None:
        digest_a = "sha256:" + "a" * 64
        digest_b = "sha256:" + "b" * 64
        configurations = {
            "docker_hub": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                "curl",
            ),
            "ecr": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                    "use_aws_ecr=true",
                ),
                "aws",
            ),
        }

        for name, (answers, fake_command_name) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                script = self.workflow_step_script(
                    destination,
                    "docker-release.yml",
                    "Inspect Docker image state",
                )
                fake_bin = destination.parent / "bin"
                fake_bin.mkdir()
                fake_command = fake_bin / fake_command_name
                if fake_command_name == "curl":
                    fake_command.write_text(
                        "#!/bin/sh\n"
                        "output=\n"
                        "url=\n"
                        "while [ \"$#\" -gt 0 ]; do\n"
                        "  case \"$1\" in\n"
                        "    --output) shift; output=$1 ;;\n"
                        "    http*) url=$1 ;;\n"
                        "  esac\n"
                        "  shift\n"
                        "done\n"
                        "case \"$url\" in\n"
                        "  */v2/auth/token) status=200; digest= ;;\n"
                        "  */tags/latest) status=${FAKE_LATEST_STATUS:-404}; digest=${FAKE_LATEST_DIGEST:-} ;;\n"
                        "  *) status=${FAKE_VERSION_STATUS:-404}; digest=${FAKE_VERSION_DIGEST:-} ;;\n"
                        "esac\n"
                        "if [ -n \"$output\" ]; then\n"
                        "  case \"$url\" in\n"
                        "    */v2/auth/token) printf '{\"access_token\":\"test-api-token\"}' > \"$output\" ;;\n"
                        "    *) printf '{\"images\":[{\"digest\":\"%s\"}]}' \"$digest\" > \"$output\" ;;\n"
                        "  esac\n"
                        "fi\n"
                        "printf '%s' \"$status\"\n"
                        "exit \"${FAKE_REGISTRY_EXIT:-0}\"\n"
                    )
                else:
                    fake_command.write_text(
                        "#!/bin/sh\n"
                        "if [ \"${FAKE_REGISTRY_EXIT:-0}\" -ne 0 ]; then\n"
                        "  exit \"$FAKE_REGISTRY_EXIT\"\n"
                        "fi\n"
                        "jq -n \\\n"
                        "  --arg tag \"${TAG:-0.1.0}\" \\\n"
                        "  --arg version \"${FAKE_VERSION_DIGEST:-}\" \\\n"
                        "  --arg latest \"${FAKE_LATEST_DIGEST:-}\" \\\n"
                        "  '{\n"
                        "    images: ([\n"
                        "      {imageId: {imageTag: $tag, imageDigest: $version}},\n"
                        "      {imageId: {imageTag: \"latest\", imageDigest: $latest}}\n"
                        "    ] | map(select(.imageId.imageDigest != \"\"))),\n"
                        "    failures: ([\n"
                        "      {imageId: {imageTag: $tag}, failureCode: (if $version == \"\" then \"ImageNotFound\" else \"\" end)},\n"
                        "      {imageId: {imageTag: \"latest\"}, failureCode: (if $latest == \"\" then \"ImageNotFound\" else \"\" end)}\n"
                        "    ] | map(select(.failureCode != \"\")))\n"
                        "  }'\n"
                    )
                fake_command.chmod(0o755)

                output_path = destination / "github-output.txt"
                base_env = {
                    **os.environ,
                    "DOCKERHUB_TOKEN": "test-token",
                    "DOCKERHUB_USERNAME": "owner",
                    "DOCKERHUB_NAMESPACE": "owner",
                    "DOCKERHUB_REPOSITORY": "project",
                    "ECR_REGISTRY_ID": "000000000000",
                    "ECR_REPOSITORY": "project",
                    "GITHUB_OUTPUT": str(output_path),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "TAG": "0.1.0",
                    "TAG_EXISTS": "true",
                }

                states = {
                    "version_missing": ({"FAKE_LATEST_DIGEST": digest_b}, False),
                    "latest_missing": ({"FAKE_VERSION_DIGEST": digest_a}, False),
                    "latest_stale": (
                        {
                            "FAKE_VERSION_DIGEST": digest_a,
                            "FAKE_LATEST_DIGEST": digest_b,
                        },
                        False,
                    ),
                    "complete": (
                        {
                            "FAKE_VERSION_DIGEST": digest_a,
                            "FAKE_LATEST_DIGEST": digest_a,
                        },
                        True,
                    ),
                }
                for state, (state_env, latest_matches) in states.items():
                    with self.subTest(registry=name, state=state):
                        output_path.unlink(missing_ok=True)
                        if fake_command_name == "curl":
                            state_env = {
                                **state_env,
                                "FAKE_VERSION_STATUS": (
                                    "200" if "FAKE_VERSION_DIGEST" in state_env else "404"
                                ),
                                "FAKE_LATEST_STATUS": (
                                    "200" if "FAKE_LATEST_DIGEST" in state_env else "404"
                                ),
                            }
                        state_result = self.run_process(
                            ["bash"],
                            destination,
                            env={**base_env, **state_env},
                            script=script,
                        )
                        self.assertEqual(
                            state_result.returncode,
                            0,
                            state_result.stdout,
                        )
                        self.assertIn(
                            f"latest_matches={str(latest_matches).lower()}",
                            output_path.read_text(),
                        )

                image_only_env = {
                    **base_env,
                    "FAKE_VERSION_DIGEST": digest_a,
                    "FAKE_VERSION_STATUS": "200",
                    "FAKE_LATEST_STATUS": "404",
                    "TAG_EXISTS": "false",
                }
                image_only_result = self.run_process(
                    ["bash"], destination, env=image_only_env, script=script
                )
                self.assertNotEqual(image_only_result.returncode, 0)
                self.assertIn("without a matching git tag", image_only_result.stdout)

                latest_only_env = {
                    **base_env,
                    "FAKE_LATEST_DIGEST": digest_b,
                    "FAKE_VERSION_STATUS": "404",
                    "FAKE_LATEST_STATUS": "200",
                    "TAG_EXISTS": "false",
                }
                latest_only_result = self.run_process(
                    ["bash"], destination, env=latest_only_env, script=script
                )
                self.assertEqual(
                    latest_only_result.returncode,
                    0,
                    latest_only_result.stdout,
                )

                invalid_digest_env = {
                    **base_env,
                    "FAKE_VERSION_DIGEST": "sha256:invalid",
                    "FAKE_VERSION_STATUS": "200",
                    "FAKE_LATEST_STATUS": "404",
                }
                invalid_digest_result = self.run_process(
                    ["bash"], destination, env=invalid_digest_env, script=script
                )
                self.assertNotEqual(invalid_digest_result.returncode, 0)
                self.assertIn("valid digest", invalid_digest_result.stdout)

                api_failure_env = {
                    **base_env,
                    "FAKE_REGISTRY_EXIT": "2",
                    "FAKE_VERSION_STATUS": "000",
                    "FAKE_LATEST_STATUS": "000",
                }
                api_failure_result = self.run_process(
                    ["bash"], destination, env=api_failure_env, script=script
                )
                self.assertNotEqual(api_failure_result.returncode, 0)
                self.assertIn("Could not", api_failure_result.stdout)

    def test_release_version_reader_accepts_and_rejects_every_version_source(
        self,
    ) -> None:
        configurations = {
            "plain": (
                "use_python=false",
                "use_gh_actions_release=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "python": (
                "use_python=true",
                "use_gh_actions_release=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "rust": (
                "use_python=false",
                "use_rust=true",
                "use_gh_actions_release=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "tauri": (
                "use_python=false",
                "use_tauri=true",
                "use_gh_actions_release=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "chrome": (
                "use_python=false",
                "use_chrome_extension=true",
                "use_gh_actions_release=true",
                "use_gh_actions_pr_tag_check=true",
            ),
        }

        for source, answers in configurations.items():
            with self.subTest(source=source):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertFalse(
                    (destination / "_release_version_reader.sh").exists(),
                )

                for workflow_name in ("release.yml", "pr-tag-check.yml"):
                    valid_result = self.run_release_version_reader(
                        destination,
                        workflow_name,
                    )
                    self.assertEqual(
                        valid_result.returncode,
                        0,
                        valid_result.stdout,
                    )
                    self.assertEqual(
                        (destination / "github-output.txt").read_text(),
                        "version=0.1.0\n",
                    )

                marker = destination / "should-not-run"
                self.write_version_source(
                    destination,
                    source,
                    "$(touch should-not-run)",
                )
                for workflow_name in ("release.yml", "pr-tag-check.yml"):
                    unsafe_result = self.run_release_version_reader(
                        destination,
                        workflow_name,
                    )
                    self.assertNotEqual(unsafe_result.returncode, 0)
                    self.assertFalse(marker.exists())
                    self.assertFalse(
                        (destination / "github-output.txt").exists(),
                    )

    def test_release_version_reader_rejects_invalid_release_tags(self) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )
        self.assertEqual(result.returncode, 0, result.stdout)

        for version in ("", "v1.2.3\nv2.0.0", "v1.lock", "v1.", "v1..2"):
            with self.subTest(version=version):
                self.write_version_source(destination, "plain", version)
                for workflow_name in ("release.yml", "pr-tag-check.yml"):
                    invalid_result = self.run_release_version_reader(
                        destination,
                        workflow_name,
                    )
                    self.assertNotEqual(invalid_result.returncode, 0)
                    self.assertFalse(
                        (destination / "github-output.txt").exists(),
                    )

    def test_docker_release_version_reader_enforces_docker_tag_format(self) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
            "use_gh_actions_pr_tag_check=true",
        )
        self.assertEqual(result.returncode, 0, result.stdout)

        for workflow_name in ("docker-release.yml", "pr-tag-check.yml"):
            valid_result = self.run_release_version_reader(
                destination,
                workflow_name,
            )
            self.assertEqual(valid_result.returncode, 0, valid_result.stdout)
            self.assertEqual(
                (destination / "github-output.txt").read_text(),
                "version=0.1.0\n",
            )

        for version in ("1.2.3+build.1", "a" * 129, "latest"):
            with self.subTest(version=version):
                self.write_version_source(destination, "plain", version)
                for workflow_name in ("docker-release.yml", "pr-tag-check.yml"):
                    invalid_result = self.run_release_version_reader(
                        destination,
                        workflow_name,
                    )
                    self.assertNotEqual(invalid_result.returncode, 0)
                    self.assertFalse(
                        (destination / "github-output.txt").exists(),
                    )

    def test_pr_tag_check_uses_validated_version_through_environment(self) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_gh_actions_pr_tag_check=true",
        )
        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()
        self.assertIn(
            "        env:\n"
            "          VERSION: ${{ steps.version.outputs.version }}\n",
            workflow,
        )
        self.assertIn("          git fetch --tags", workflow)
        self.assertIn(
            'git show-ref --tags --verify --quiet "refs/tags/$VERSION"',
            workflow,
        )

        for step_name in (
            "Check if tag exists",
            "Build release version availability summary",
        ):
            script = self.workflow_step_script(
                destination,
                "pr-tag-check.yml",
                step_name,
            )
            self.assertNotIn("steps.version.outputs.version", script)

    def test_pr_tag_check_classifies_release_version_availability(self) -> None:
        plain_result, plain_destination = self.copy_template(
            "use_python=false",
            "use_gh_actions_pr_tag_check=true",
        )
        self.assertEqual(plain_result.returncode, 0, plain_result.stdout)

        plain_workflow = (
            plain_destination / ".github/workflows/pr-tag-check.yml"
        ).read_text()
        self.assertIn("name: Check if GitHub Release exists", plain_workflow)
        self.assertNotIn("name: Check Docker Hub image tag", plain_workflow)
        self.assertNotIn("name: Check ECR image tag", plain_workflow)
        self.assertNotIn("id-token: write", plain_workflow)

        fake_bin = plain_destination.parent / "bin"
        fake_bin.mkdir()
        fake_curl = fake_bin / "curl"
        fake_curl.write_text(
            "#!/bin/sh\n"
            "printf '%s' \"${FAKE_HTTP_STATUS:-404}\"\n"
            "exit \"${FAKE_CURL_EXIT:-0}\"\n"
        )
        fake_curl.chmod(0o755)
        release_script = self.workflow_step_script(
            plain_destination,
            "pr-tag-check.yml",
            "Check if GitHub Release exists",
        )
        release_output = plain_destination / "release-output.txt"
        release_env = {
            **os.environ,
            "GH_TOKEN": "test-token",
            "GITHUB_API_URL": "https://api.github.example",
            "GITHUB_OUTPUT": str(release_output),
            "GITHUB_REPOSITORY": "owner/project",
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "VERSION": "0.1.0",
        }
        for http_status, expected_exists in (("200", "true"), ("404", "false")):
            with self.subTest(signal="release", http_status=http_status):
                release_output.unlink(missing_ok=True)
                release_check = self.run_process(
                    ["bash"],
                    plain_destination,
                    env={**release_env, "FAKE_HTTP_STATUS": http_status},
                    script=release_script,
                )
                self.assertEqual(
                    release_check.returncode,
                    0,
                    release_check.stdout,
                )
                self.assertIn(
                    f"exists={expected_exists}",
                    release_output.read_text(),
                )

        release_failure = self.run_process(
            ["bash"],
            plain_destination,
            env={**release_env, "FAKE_HTTP_STATUS": "500"},
            script=release_script,
        )
        self.assertNotEqual(release_failure.returncode, 0)
        self.assertIn("HTTP 500", release_failure.stdout)

        docker_hub_result, docker_hub_destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
            "use_gh_actions_pr_tag_check=true",
            "docker_registry=mizucopo",
            "docker_image_name=test-project",
        )
        self.assertEqual(
            docker_hub_result.returncode,
            0,
            docker_hub_result.stdout,
        )
        docker_hub_workflow = (
            docker_hub_destination / ".github/workflows/pr-tag-check.yml"
        ).read_text()
        self.assertIn("name: Check Docker Hub image tag", docker_hub_workflow)
        self.assertIn(
            "https://auth.docker.io/token",
            docker_hub_workflow,
        )
        self.assertIn(
            "https://registry-1.docker.io/v2/$DOCKERHUB_NAMESPACE/"
            "$DOCKERHUB_REPOSITORY/manifests/$VERSION",
            docker_hub_workflow,
        )
        self.assertIn("              --head \\\n", docker_hub_workflow)
        self.assertNotIn("--request HEAD", docker_hub_workflow)
        self.assertNotIn("https://hub.docker.com/v2/auth/token", docker_hub_workflow)
        self.assertNotIn("${{ secrets.DOCKERHUB_TOKEN }}", docker_hub_workflow)
        self.assertNotIn("DOCKERHUB_API_TOKEN", docker_hub_workflow)
        self.assertNotIn("name: Check ECR image tag", docker_hub_workflow)
        self.assertNotIn("id-token: write", docker_hub_workflow)

        docker_hub_fake_bin = docker_hub_destination.parent / "bin"
        docker_hub_fake_bin.mkdir()
        fake_docker_hub_curl = docker_hub_fake_bin / "curl"
        fake_docker_hub_curl.write_text(
            "#!/bin/sh\n"
            "output=/dev/null\n"
            "url=\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in\n"
            "    --output)\n"
            "      output=\"$2\"\n"
            "      shift 2\n"
            "      ;;\n"
            "    http*)\n"
            "      url=\"$1\"\n"
            "      shift\n"
            "      ;;\n"
            "    *)\n"
            "      shift\n"
            "      ;;\n"
            "  esac\n"
            "done\n"
            "case \"$url\" in\n"
            "  *auth.docker.io/token)\n"
            "    printf '{\"token\":\"test-pull-token\"}' > \"$output\"\n"
            "    printf '%s' \"${FAKE_TOKEN_HTTP_STATUS:-200}\"\n"
            "    ;;\n"
            "  *registry-1.docker.io*/manifests/*)\n"
            "    printf '%s' \"${FAKE_TAG_HTTP_STATUS:-404}\"\n"
            "    ;;\n"
            "  *)\n"
            "    exit 1\n"
            "    ;;\n"
            "esac\n"
            "exit \"${FAKE_CURL_EXIT:-0}\"\n"
        )
        fake_docker_hub_curl.chmod(0o755)
        docker_hub_script = self.workflow_step_script(
            docker_hub_destination,
            "pr-tag-check.yml",
            "Check Docker Hub image tag",
        )
        docker_hub_output = docker_hub_destination / "docker-hub-output.txt"
        docker_hub_env = {
            **os.environ,
            "DOCKERHUB_NAMESPACE": "mizucopo",
            "DOCKERHUB_REPOSITORY": "test-project",
            "GITHUB_OUTPUT": str(docker_hub_output),
            "PATH": f"{docker_hub_fake_bin}{os.pathsep}{os.environ['PATH']}",
            "VERSION": "0.1.0",
        }
        for http_status, expected_exists in (("200", "true"), ("404", "false")):
            with self.subTest(signal="docker_hub", http_status=http_status):
                docker_hub_output.unlink(missing_ok=True)
                docker_hub_check = self.run_process(
                    ["bash"],
                    docker_hub_destination,
                    env={**docker_hub_env, "FAKE_TAG_HTTP_STATUS": http_status},
                    script=docker_hub_script,
                )
                self.assertEqual(
                    docker_hub_check.returncode,
                    0,
                    docker_hub_check.stdout,
                )
                self.assertIn(
                    f"exists={expected_exists}",
                    docker_hub_output.read_text(),
                )

        docker_hub_failure = self.run_process(
            ["bash"],
            docker_hub_destination,
            env={**docker_hub_env, "FAKE_TAG_HTTP_STATUS": "500"},
            script=docker_hub_script,
        )
        self.assertNotEqual(docker_hub_failure.returncode, 0)
        self.assertIn("HTTP 500", docker_hub_failure.stdout)

        private_docker_hub_failure = self.run_process(
            ["bash"],
            docker_hub_destination,
            env={**docker_hub_env, "FAKE_TAG_HTTP_STATUS": "401"},
            script=docker_hub_script,
        )
        self.assertNotEqual(private_docker_hub_failure.returncode, 0)
        self.assertIn("HTTP 401", private_docker_hub_failure.stdout)

        ecr_result, ecr_destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
            "use_gh_actions_pr_tag_check=true",
            "use_aws_ecr=true",
            "aws_account_id=123456789012",
            "aws_region=ap-northeast-1",
            "docker_image_name=test-project",
        )
        self.assertEqual(ecr_result.returncode, 0, ecr_result.stdout)
        ecr_workflow = (
            ecr_destination / ".github/workflows/pr-tag-check.yml"
        ).read_text()
        self.assertIn("id-token: write", ecr_workflow)
        self.assertIn("name: Configure AWS Credentials", ecr_workflow)
        self.assertIn("name: Check ECR image tag", ecr_workflow)
        self.assertIn("aws ecr batch-get-image", ecr_workflow)
        self.assertNotIn("name: Check Docker Hub image tag", ecr_workflow)

        ecr_fake_bin = ecr_destination.parent / "bin"
        ecr_fake_bin.mkdir()
        fake_aws = ecr_fake_bin / "aws"
        fake_aws.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"${FAKE_AWS_RESPONSE}\"\n"
            "exit \"${FAKE_AWS_EXIT:-0}\"\n"
        )
        fake_aws.chmod(0o755)
        ecr_script = self.workflow_step_script(
            ecr_destination,
            "pr-tag-check.yml",
            "Check ECR image tag",
        )
        ecr_output = ecr_destination / "ecr-output.txt"
        ecr_env = {
            **os.environ,
            "ECR_REGISTRY_ID": "123456789012",
            "ECR_REPOSITORY": "test-project",
            "GITHUB_OUTPUT": str(ecr_output),
            "PATH": f"{ecr_fake_bin}{os.pathsep}{os.environ['PATH']}",
            "VERSION": "0.1.0",
        }
        ecr_states = {
            "existing": (
                {
                    "images": [
                        {
                            "imageId": {
                                "imageTag": "0.1.0",
                                "imageDigest": "sha256:" + "a" * 64,
                            }
                        }
                    ],
                    "failures": [],
                },
                "true",
            ),
            "missing": (
                {
                    "images": [],
                    "failures": [
                        {
                            "imageId": {"imageTag": "0.1.0"},
                            "failureCode": "ImageNotFound",
                        }
                    ],
                },
                "false",
            ),
        }
        for state, (response, expected_exists) in ecr_states.items():
            with self.subTest(signal="ecr", state=state):
                ecr_output.unlink(missing_ok=True)
                ecr_check = self.run_process(
                    ["bash"],
                    ecr_destination,
                    env={**ecr_env, "FAKE_AWS_RESPONSE": json.dumps(response)},
                    script=ecr_script,
                )
                self.assertEqual(ecr_check.returncode, 0, ecr_check.stdout)
                self.assertIn(f"exists={expected_exists}", ecr_output.read_text())

        ambiguous_ecr = self.run_process(
            ["bash"],
            ecr_destination,
            env={
                **ecr_env,
                "FAKE_AWS_RESPONSE": json.dumps({"images": [], "failures": []}),
            },
            script=ecr_script,
        )
        self.assertNotEqual(ambiguous_ecr.returncode, 0)
        self.assertIn("no verifiable state", ambiguous_ecr.stdout)

        summary_script = self.workflow_step_script(
            ecr_destination,
            "pr-tag-check.yml",
            "Build release version availability summary",
        )
        summary_path = ecr_destination / "summary.md"
        summary_output = ecr_destination / "summary-output.txt"
        summary_env = {
            **os.environ,
            "AWS_CREDENTIALS_OUTCOME": "success",
            "GITHUB_OUTPUT": str(summary_output),
            "GITHUB_STEP_SUMMARY": str(summary_path),
            "IMAGE_EXISTS": "false",
            "IMAGE_OUTCOME": "success",
            "IMAGE_REPOSITORY": "123456789012.dkr.ecr.ap-northeast-1.amazonaws.com/test-project",
            "RELEASE_EXISTS": "false",
            "RELEASE_OUTCOME": "success",
            "TAG_EXISTS": "false",
            "TAG_OUTCOME": "success",
            "VERSION": "0.1.0",
            "VERSION_OUTCOME": "success",
        }
        available_summary_result = self.run_process(
            ["bash"],
            ecr_destination,
            env=summary_env,
            script=summary_script,
        )
        self.assertEqual(
            available_summary_result.returncode,
            0,
            available_summary_result.stdout,
        )
        self.assertIn("Release Version Availability ✅", summary_path.read_text())
        available_summary_outputs = summary_output.read_text()
        self.assertIn(
            "availability_check_completed=true",
            available_summary_outputs,
        )
        self.assertIn("availability_conflict=false", available_summary_outputs)

        summary_path.unlink()
        summary_output.unlink()
        summary_result = self.run_process(
            ["bash"],
            ecr_destination,
            env={
                **summary_env,
                "IMAGE_EXISTS": "true",
                "RELEASE_EXISTS": "true",
                "TAG_EXISTS": "true",
            },
            script=summary_script,
        )
        self.assertEqual(summary_result.returncode, 0, summary_result.stdout)
        summary = summary_path.read_text()
        self.assertIn("already exists as a git tag", summary)
        self.assertIn("already exists as a GitHub Release", summary)
        self.assertIn("already exists as an ECR image tag", summary)
        summary_outputs = summary_output.read_text()
        self.assertIn("availability_check_completed=true", summary_outputs)
        self.assertIn("availability_conflict=true", summary_outputs)

        self.assertIn(
            "steps.tag-report.outputs.availability_check_completed != 'true'",
            ecr_workflow,
        )
        self.assertIn(
            "steps.tag-report.outputs.availability_conflict != 'false'",
            ecr_workflow,
        )

    def test_long_chrome_extension_name_is_already_formatted(self) -> None:
        long_name = "Very Long Chrome Extension Name For Formatting"

        result, destination = self.copy_template(
            "use_chrome_extension=true",
            f"chrome_extension_name={long_name}",
        )
        self.assertEqual(result.returncode, 0, result.stdout)

        install_result = subprocess.run(
            ["npm", "install", "--no-audit", "--prefix", str(destination)],
            check=False,
            env={**os.environ, "npm_config_cache": str(NPM_CACHE)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.assertEqual(install_result.returncode, 0, install_result.stdout)

        format_result = subprocess.run(
            ["npm", "--prefix", str(destination), "run", "format:check"],
            check=False,
            env={**os.environ, "npm_config_cache": str(NPM_CACHE)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.assertEqual(format_result.returncode, 0, format_result.stdout)

    def test_invalid_chrome_manifest_version_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_chrome_extension=true",
            "chrome_extension_version=1.0.0-beta.1",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chrome Extension バージョン", result.stdout)

    def test_existing_chrome_source_is_preserved_and_manifest_recopies(self) -> None:
        destination_root = tempfile.TemporaryDirectory()
        self.addCleanup(destination_root.cleanup)

        destination = Path(destination_root.name) / "existing-extension"
        (destination / "src").mkdir(parents=True)
        (destination / "tests").mkdir()
        (destination / ".github/workflows").mkdir(parents=True)

        existing_files = {
            "package.json": json.dumps(
                {
                    "name": "voice-live-comment",
                    "version": "1.2.3",
                    "scripts": {"test": "node tests/existing.test.js"},
                }
            )
            + "\n",
            "src/manifest.json": '{"manifest_version":3,"name":"Legacy Extension"}\n',
            "src/background.ts": "console.log('legacy background');\n",
            "tests/lib/extension-title.test.ts": "throw new Error('legacy test');\n",
            ".github/workflows/chrome-extension-quality-checks.yml": "name: Legacy Quality\n",
        }
        for relative_path, content in existing_files.items():
            (destination / relative_path).parent.mkdir(parents=True, exist_ok=True)
            (destination / relative_path).write_text(content)

        pretend_result = self.copy_template_into(
            destination,
            "use_chrome_extension=true",
            "chrome_extension_name=Standard Extension",
            "chrome_extension_version=2.0.0",
            overwrite=True,
            pretend=True,
        )
        self.assertEqual(pretend_result.returncode, 0, pretend_result.stdout)
        for relative_path, content in existing_files.items():
            self.assertEqual((destination / relative_path).read_text(), content)

        result = self.copy_template_into(
            destination,
            "use_chrome_extension=true",
            "chrome_extension_name=Standard Extension",
            "chrome_extension_version=2.0.0",
            overwrite=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        answers_path = destination / ".copier-answers.yml"
        answers_path.write_text(
            answers_path.read_text()
            + "chrome_extension_mode: adopt_existing\n"
            + "chrome_extension_manifest_path: manifest.json\n"
        )
        recopy_result = self.recopy_template(destination)
        self.assertEqual(recopy_result.returncode, 0, recopy_result.stdout)

        package = json.loads((destination / "package.json").read_text())
        manifest = json.loads((destination / "src/manifest.json").read_text())
        self.assertEqual(package["version"], "2.0.0")
        self.assertEqual(manifest["name"], "Standard Extension")
        self.assertEqual(manifest["version"], "2.0.0")
        self.assertEqual(
            (destination / "src/background.ts").read_text(),
            "console.log('legacy background');\n",
        )
        self.assertEqual(
            (destination / "tests/lib/extension-title.test.ts").read_text(),
            "throw new Error('legacy test');\n",
        )

        answers = answers_path.read_text()
        self.assertIn("use_chrome_extension: true", answers)
        self.assertNotIn("chrome_extension_mode", answers)
        for standard_path in (
            "src/background.ts",
            "src/popup.ts",
            "src/popup.html",
            "src/popup.css",
            "src/lib/extension-title.ts",
            "tests/lib/extension-title.test.ts",
            "scripts/copy-extension-assets.mjs",
            "scripts/clean-dist.mjs",
            ".github/workflows/chrome-extension-quality-checks.yml",
            "tsconfig.json",
            "tsconfig.build.json",
            "eslint.config.mjs",
            "vitest.config.ts",
            ".prettierrc.json",
            ".prettierignore",
        ):
            self.assertTrue((destination / standard_path).exists(), standard_path)

    def test_chrome_version_source_wins_when_python_and_rust_are_enabled(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_rust=true",
            "use_chrome_extension=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("package.json", release_workflow)
        self.assertIn("package.json", tag_check_workflow)
        self.assertNotIn("Cargo.toml", release_workflow)
        self.assertNotIn("Cargo.toml", tag_check_workflow)

    def test_chrome_pr_tag_check_validates_scaffold_manifest_version_source(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_pr_tag_check=true",
            "chrome_extension_version=1.2.3",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()
        self.assertIn('"src/manifest.json"', workflow)
        self.assertIn("manifestVersion", workflow)
        self.assertIn("Release version source validation failed", workflow)
        self.assertIn(
            "Enforce version tag availability",
            workflow,
        )

        valid_result = self.run_pr_tag_version_reader(destination)
        self.assertEqual(valid_result.returncode, 0, valid_result.stdout)
        output = (destination / "github-output.txt").read_text()
        self.assertIn("version=1.2.3", output)

        manifest_path = destination / "src/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = "1.2.4"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

        mismatch_result = self.run_pr_tag_version_reader(destination)
        self.assertNotEqual(mismatch_result.returncode, 0)
        version_error = (destination / "version_check_error.txt").read_text()
        self.assertIn(
            'package.json version "1.2.3" does not match src/manifest.json '
            'version "1.2.4"',
            version_error,
        )

    def test_chrome_pr_tag_check_rejects_invalid_manifest_version(self) -> None:
        result, destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        manifest_path = destination / "src/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = "1.2.3-beta.1"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

        invalid_result = self.run_pr_tag_version_reader(destination)
        self.assertNotEqual(invalid_result.returncode, 0)
        version_error = (destination / "version_check_error.txt").read_text()
        self.assertIn("Chrome manifest version", version_error)
        self.assertIn("1 to 4 dot-separated integers", version_error)

    def test_pr_tag_check_fails_closed_for_every_version_source(self) -> None:
        configurations = {
            "python": (
                "use_python=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "rust": (
                "use_python=false",
                "use_rust=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "tauri": (
                "use_python=false",
                "use_tauri=true",
                "use_gh_actions_pr_tag_check=true",
            ),
            "chrome": (
                "use_python=false",
                "use_chrome_extension=true",
                "use_gh_actions_pr_tag_check=true",
            ),
        }

        for name, answers in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                workflow = (
                    destination / ".github/workflows/pr-tag-check.yml"
                ).read_text()
                self.assertIn('let checkConclusion = "failure";', workflow)
                self.assertIn(
                    'VERSION_SOURCE_VALIDATION_FAILED="true"',
                    workflow,
                )
                self.assertIn(
                    "Release version source validation failed",
                    workflow,
                )
                self.assertNotIn('checkConclusion = "neutral";', workflow)
                self.assertIn(
                    "name: Enforce version tag availability",
                    workflow,
                )
                self.assertIn(
                    "steps.tag-report.outputs.availability_check_completed "
                    "!= 'true'",
                    workflow,
                )
                self.assertIn(
                    "steps.tag-report.outputs.availability_conflict != 'false'",
                    workflow,
                )
                self.assertIn("run: exit 1", workflow)

    def test_chrome_distribution_release_workflow_is_opt_in(self) -> None:
        result, destination = self.copy_template("use_chrome_extension=true")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(
            (destination / ".github/workflows/chrome-extension-release.yml").exists()
        )

    def test_chrome_distribution_release_workflow_renders_metadata_and_guards(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
            "chrome_extension_release_package_root_directory=.",
            "chrome_extension_release_zip_name=voice-live-comment-{version}.zip",
            "chrome_extension_release_title=Voice Live Comment {version}",
            "chrome_extension_release_notes=Release notes for {version}.",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (
            destination / ".github/workflows/chrome-extension-release.yml"
        ).read_text()

        self.assertIn("on:\n  push:", workflow)
        self.assertIn("branches:\n      - main", workflow)
        self.assertIn("Verify merged PR commit checkout", workflow)
        self.assertIn("commits/${RELEASE_SHA}/pulls", workflow)
        self.assertIn(".merged_at != null and .base.ref == \"main\"", workflow)
        self.assertNotIn("PARENT_COUNT", workflow)
        self.assertIn('const packageRoot = ".";', workflow)
        self.assertIn(
            'const zipNameTemplate = "voice-live-comment-{version}.zip";',
            workflow,
        )
        self.assertIn("npm ci", workflow)
        self.assertIn("npm run check", workflow)
        self.assertIn("npm run build", workflow)
        self.assertIn("Validate distribution manifest", workflow)
        self.assertIn("zip -r", workflow)
        self.assertIn("zip_args=(", workflow)
        self.assertIn('zip -r "$ZIP_PATH" . "${zip_args[@]}"', workflow)
        self.assertNotIn('zip -r "$ZIP_PATH" . \\', workflow)
        self.assertIn('zipName.includes("#")', workflow)
        self.assertIn("GIT_USER_NAME:", workflow)
        self.assertIn("GIT_USER_EMAIL:", workflow)
        self.assertIn('git config user.name "$GIT_USER_NAME"', workflow)
        self.assertIn('git config user.email "$GIT_USER_EMAIL"', workflow)
        self.assertNotIn("steps.author.outputs", workflow)
        self.assertIn("git rev-list -n 1", workflow)
        self.assertIn("already points to", workflow)
        self.assertIn("/releases/tags/$TAG", workflow)
        self.assertIn('case "$RELEASE_HTTP_STATUS"', workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertNotIn("--clobber", workflow)
        for excluded_path in (
            ".copier-answers.yml",
            ".node-version",
            ".gitignore",
            "AGENTS.md",
            "CLAUDE.md",
            "README.md",
        ):
            self.assertIn(f'-x "{excluded_path}"', workflow)

        metadata_result = self.run_chrome_release_metadata_reader(destination)
        self.assertEqual(metadata_result.returncode, 0, metadata_result.stdout)
        output = (destination / "github-output.txt").read_text()
        self.assertIn("version=0.1.0", output)
        self.assertIn("tag=0.1.0", output)
        self.assertIn("manifest_path=src/manifest.json", output)
        self.assertIn("fallback_distribution_root=src", output)
        self.assertIn("zip_name=voice-live-comment-0.1.0.zip", output)
        self.assertIn("release_title=Voice Live Comment 0.1.0", output)
        self.assertIn("release_notes_path=", output)
        self.assertEqual(
            (destination / "runner-temp/release-notes.md").read_text(),
            "Release notes for 0.1.0.\n",
        )
        self.assertFalse((destination / "release-notes.md").exists())

    def test_chrome_distribution_release_workflow_uses_package_root_answer(
        self,
    ) -> None:
        destination_root = tempfile.TemporaryDirectory()
        self.addCleanup(destination_root.cleanup)

        destination = Path(destination_root.name) / "existing-extension"
        (destination / "extension/src").mkdir(parents=True)
        (destination / "extension/package.json").write_text(
            json.dumps({"name": "existing-extension", "version": "3.4.5"}) + "\n"
        )
        (destination / "extension/src/manifest.json").write_text(
            '{"manifest_version":3,"version":"3.4.5"}\n'
        )

        result = self.copy_template_into(
            destination,
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
            "chrome_extension_release_package_root_directory=extension",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (
            destination / ".github/workflows/chrome-extension-release.yml"
        ).read_text()
        self.assertIn('const packageRoot = "extension";', workflow)
        self.assertIn('packageRoot.replaceAll("\\\\", "/")', workflow)
        self.assertIn('"src/manifest.json"', workflow)
        self.assertIn('node-version-file: ".node-version"', workflow)
        self.assertIn('-x "src/*"', workflow)
        self.assertIn('-x "scripts/*"', workflow)

        metadata_result = self.run_chrome_release_metadata_reader(destination)
        self.assertEqual(metadata_result.returncode, 0, metadata_result.stdout)
        output = (destination / "github-output.txt").read_text()
        self.assertIn("package_root=extension", output)
        self.assertIn("version=3.4.5", output)
        self.assertIn("manifest_path=extension/src/manifest.json", output)
        self.assertIn("fallback_distribution_root=src", output)

        valid_distribution_result = (
            self.run_chrome_release_distribution_manifest_validator(
                destination,
                package_root="extension",
                distribution_root="src",
                expected_version="3.4.5",
            )
        )
        self.assertEqual(
            valid_distribution_result.returncode,
            0,
            valid_distribution_result.stdout,
        )

        tag_check_result = self.run_pr_tag_version_reader(destination)
        self.assertEqual(tag_check_result.returncode, 0, tag_check_result.stdout)
        output = (destination / "github-output.txt").read_text()
        self.assertIn("version=3.4.5", output)

    def test_chrome_distribution_release_workflow_normalizes_package_root_answer(
        self,
    ) -> None:
        destination_root = tempfile.TemporaryDirectory()
        self.addCleanup(destination_root.cleanup)

        destination = Path(destination_root.name) / "existing-extension"
        (destination / "extension/app/src").mkdir(parents=True)
        (destination / "extension/app/package.json").write_text(
            json.dumps({"name": "existing-extension", "version": "4.5.6"}) + "\n"
        )
        (destination / "extension/app/src/manifest.json").write_text(
            '{"manifest_version":3,"version":"4.5.6"}\n'
        )

        result = self.copy_template_into(
            destination,
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
            r"chrome_extension_release_package_root_directory=extension\app",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (
            destination / ".github/workflows/chrome-extension-release.yml"
        ).read_text()
        self.assertIn('const packageRoot = "extension/app";', workflow)
        self.assertIn('node-version-file: ".node-version"', workflow)

        metadata_result = self.run_chrome_release_metadata_reader(destination)
        self.assertEqual(metadata_result.returncode, 0, metadata_result.stdout)
        output = (destination / "github-output.txt").read_text()
        self.assertIn("package_root=extension/app", output)
        self.assertIn("version=4.5.6", output)
        self.assertIn("fallback_distribution_root=src", output)

        tag_check_result = self.run_pr_tag_version_reader(destination)
        self.assertEqual(tag_check_result.returncode, 0, tag_check_result.stdout)
        output = (destination / "github-output.txt").read_text()
        self.assertIn("version=4.5.6", output)

    def test_chrome_distribution_release_workflow_validates_uploaded_manifest(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        (destination / "dist").mkdir()
        (destination / "dist/manifest.json").write_text(
            '{"manifest_version":3,"version":"0.1.1"}\n'
        )

        invalid_result = self.run_chrome_release_distribution_manifest_validator(
            destination,
            package_root=".",
            distribution_root="dist",
            expected_version="0.1.0",
        )
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertIn(
            'does not match package.json version "0.1.0"',
            invalid_result.stdout,
        )

        (destination / "dist/manifest.json").write_text(
            '{"manifest_version":3,"version":"0.1.0"}\n'
        )
        valid_result = self.run_chrome_release_distribution_manifest_validator(
            destination,
            package_root=".",
            distribution_root="dist",
            expected_version="0.1.0",
        )
        self.assertEqual(valid_result.returncode, 0, valid_result.stdout)

    def test_chrome_distribution_release_rejects_asset_label_separator_in_zip_name(
        self,
    ) -> None:
        result, _destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
            r"chrome_extension_release_zip_name=extension#{version}.zip",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chrome Extension 配布 zip 名", result.stdout)
        self.assertIn("#", result.stdout)

    def test_chrome_distribution_release_rejects_other_release_workflows(
        self,
    ) -> None:
        generic_result, _generic_destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_release=true",
            "use_gh_actions_chrome_extension_release=true",
        )
        self.assertNotEqual(generic_result.returncode, 0)
        self.assertIn(
            "Chrome Extension配布release workflow",
            generic_result.stdout,
        )
        self.assertIn("use_gh_actions_release", generic_result.stdout)

        docker_result, _docker_destination = self.copy_template(
            "use_chrome_extension=true",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
            "use_gh_actions_chrome_extension_release=true",
        )
        self.assertNotEqual(docker_result.returncode, 0)
        self.assertIn(
            "Chrome Extension配布release workflow",
            docker_result.stdout,
        )
        self.assertIn("use_gh_actions_docker_release", docker_result.stdout)

    def test_python_version_source_wins_when_rust_is_also_enabled(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_rust=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("pyproject.toml", release_workflow)
        self.assertIn("pyproject.toml", tag_check_workflow)
        self.assertNotIn("Cargo.toml", release_workflow)
        self.assertNotIn("Cargo.toml", tag_check_workflow)

    def test_rust_template_generates_cargo_project(self) -> None:
        result, destination = self.copy_template(
            "use_rust=true",
            "rust_version=1.88.0",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        cargo_toml = (destination / "Cargo.toml").read_text()
        rust_toolchain = (destination / "rust-toolchain.toml").read_text()
        main_rs = (destination / "src/main.rs").read_text()
        rust_workflow = (
            destination / ".github/workflows/rust-quality-checks.yml"
        ).read_text()

        self.assertIn('name = "test-project"', cargo_toml)
        self.assertIn('version = "0.1.0"', cargo_toml)
        self.assertIn('channel = "1.88.0"', rust_toolchain)
        self.assertIn('println!("Hello, world!");', main_rs)
        self.assertIn("cargo fmt --all --check", rust_workflow)
        self.assertIn("cargo clippy --all-targets --all-features", rust_workflow)
        self.assertIn("cargo test --all-targets --all-features", rust_workflow)
        self.assertFalse((destination / "version").exists())

    def test_rust_version_source_is_used_when_no_higher_priority_runtime_exists(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_rust=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("Cargo.toml", release_workflow)
        self.assertIn("Cargo.toml", tag_check_workflow)
        self.assertNotIn("cat version", release_workflow)
        self.assertNotIn("cat version", tag_check_workflow)

    def test_rust_version_source_is_used_for_docker_release(self) -> None:
        result, destination = self.copy_template(
            "use_rust=true",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        docker_release_workflow = (
            destination / ".github/workflows/docker-release.yml"
        ).read_text()

        self.assertIn("Cargo.toml", docker_release_workflow)
        self.assertNotIn("cat version", docker_release_workflow)

    def test_rust_toolchain_older_than_edition_2024_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_rust=true",
            "rust_version=1.84.1",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Rust ツールチェーン", result.stdout)

    def test_tauri_template_generates_desktop_project(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "rust_version=1.88.0",
            "node_version=24",
            "tauri_product_name=Desk App",
            "tauri_identifier=com.example.desk",
            "tauri_version=1.2.3",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        package = json.loads((destination / "package.json").read_text())
        tauri_config = json.loads((destination / "src-tauri/tauri.conf.json").read_text())
        cargo_toml = (destination / "src-tauri/Cargo.toml").read_text()
        rust_toolchain = (destination / "rust-toolchain.toml").read_text()
        workflow = (destination / ".github/workflows/tauri-quality-checks.yml").read_text()

        self.assertEqual(package["version"], "1.2.3")
        self.assertEqual(package["dependencies"]["@tauri-apps/api"], "^2.11.1")
        self.assertEqual(tauri_config["productName"], "Desk App")
        self.assertEqual(tauri_config["identifier"], "com.example.desk")
        self.assertEqual(tauri_config["version"], "1.2.3")
        self.assertIn("icons/icon.png", tauri_config["bundle"]["icon"])
        self.assertIn('version = "1.2.3"', cargo_toml)
        self.assertIn('channel = "1.88.0"', rust_toolchain)
        self.assertIn("npm run cargo:clippy", workflow)
        self.assertIn("libwebkit2gtk-4.1-dev", workflow)
        self.assertIn("libxdo-dev", workflow)
        self.assertTrue((destination / "src-tauri/icons/icon.png").exists())
        self.assertFalse((destination / "Cargo.toml").exists())
        self.assertFalse((destination / "src/main.rs").exists())
        self.assertFalse((destination / "version").exists())

    def test_tauri_package_name_configures_internal_identity(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "tauri_package_name=mizu-pairrank",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        package = json.loads((destination / "package.json").read_text())
        cargo_toml = (destination / "src-tauri/Cargo.toml").read_text()
        main_rs = (destination / "src-tauri/src/main.rs").read_text()

        self.assertEqual(package["name"], "mizu-pairrank")
        self.assertIn('name = "mizu-pairrank"', cargo_toml)
        self.assertIn('name = "mizu_pairrank_lib"', cargo_toml)
        self.assertIn("mizu_pairrank_lib::run()", main_rs)

    def test_tauri_package_name_rejects_non_kebab_case(self) -> None:
        for package_name in (
            "Mizu-pairrank",
            "1mizu-pairrank",
            "mizu_pairrank",
            "-mizu-pairrank",
            "mizu-pairrank-",
            "mizu--pairrank",
            "ミズ-pairrank",
        ):
            with self.subTest(package_name=package_name):
                result, _destination = self.copy_template(
                    "use_tauri=true",
                    f"tauri_package_name={package_name}",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Tauri package 名", result.stdout)

    def test_tauri_package_name_rejects_more_than_64_characters(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            f"tauri_package_name={'a' * 65}",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("64文字以内", result.stdout)

    def test_tauri_package_name_preserves_legacy_default_and_survives_recopy(
        self,
    ) -> None:
        result, destination = self.copy_template("use_tauri=true")
        self.assertEqual(result.returncode, 0, result.stdout)

        answers_file = destination / ".copier-answers.yml"
        answers = answers_file.read_text()
        self.assertIn("tauri_package_name: test-tauri-app", answers)

        answers_file.write_text(
            answers.replace("tauri_package_name: test-tauri-app\n", "")
        )
        legacy_recopy = self.recopy_template(destination)
        self.assertEqual(legacy_recopy.returncode, 0, legacy_recopy.stdout)
        self.assertEqual(
            json.loads((destination / "package.json").read_text())["name"],
            "test-tauri-app",
        )

        answers_file.write_text(
            answers_file.read_text().replace(
                "tauri_package_name: test-tauri-app",
                "tauri_package_name: mizu-pairrank",
            )
        )
        configured_recopy = self.recopy_template(destination)
        self.assertEqual(configured_recopy.returncode, 0, configured_recopy.stdout)

        package = json.loads((destination / "package.json").read_text())
        cargo_toml = (destination / "src-tauri/Cargo.toml").read_text()
        main_rs = (destination / "src-tauri/src/main.rs").read_text()
        self.assertEqual(package["name"], "mizu-pairrank")
        self.assertIn('name = "mizu-pairrank"', cargo_toml)
        self.assertIn('name = "mizu_pairrank_lib"', cargo_toml)
        self.assertIn("mizu_pairrank_lib::run()", main_rs)

    def test_readme_documents_tauri_package_name_and_recopy_migration(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text()

        for expected_guidance in (
            "tauri_package_name",
            "tauri_product_name",
            "test-tauri-app",
            "mizu-pairrank",
            "copier recopy -f",
            "src-tauri/Cargo.toml",
            "src-tauri/src/main.rs",
        ):
            with self.subTest(guidance=expected_guidance):
                self.assertIn(expected_guidance, readme)

    def test_template_design_adr_is_not_generated(self) -> None:
        result, destination = self.copy_template("use_tauri=true")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(
            (
                destination
                / "docs/adr/0001-preserve-legacy-tauri-package-name.md"
            ).exists()
        )

    def test_tauri_values_are_preserved_in_json_outputs(self) -> None:
        product_name = "Desk & App's Name"

        result, destination = self.copy_template(
            "use_tauri=true",
            f"tauri_product_name={product_name}",
            "tauri_identifier=com.example.escaped",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        package = json.loads((destination / "package.json").read_text())
        tauri_config = json.loads((destination / "src-tauri/tauri.conf.json").read_text())

        self.assertEqual(package["description"], product_name)
        self.assertEqual(tauri_config["productName"], product_name)

    def test_tauri_html_product_name_is_escaped(self) -> None:
        product_name = "ACME & Beta"

        result, destination = self.copy_template(
            "use_tauri=true",
            f"tauri_product_name={product_name}",
            "tauri_identifier=com.example.escaped",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        index_html = (destination / "index.html").read_text()

        self.assertIn("ACME &amp; Beta", index_html)
        self.assertNotIn(product_name, index_html)

    def test_invalid_tauri_product_name_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_product_name=Bad/Name",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリ名", result.stdout)

    def test_tauri_identifier_allows_hyphenated_segments(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "tauri_identifier=com.example.my-app",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        tauri_config = json.loads((destination / "src-tauri/tauri.conf.json").read_text())

        self.assertEqual(tauri_config["identifier"], "com.example.my-app")

    def test_invalid_tauri_identifier_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_identifier=invalid",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリ識別子", result.stdout)

    def test_tauri_identifier_with_underscore_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_identifier=com.example.my_app",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリ識別子", result.stdout)

    def test_invalid_tauri_version_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_version=1.0",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリバージョン", result.stdout)

    def test_tauri_version_with_empty_prerelease_segment_is_rejected(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "tauri_version=1.2.3-..",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri アプリバージョン", result.stdout)

    def test_tauri_cannot_be_combined_with_conflicting_runtime_support(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "use_chrome_extension=true",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri と Chrome Extension", result.stdout)

    def test_tauri_cannot_be_combined_with_root_rust_runtime_support(self) -> None:
        result, _destination = self.copy_template(
            "use_tauri=true",
            "use_rust=true",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tauri は専用の src-tauri", result.stdout)

    def test_tauri_version_source_wins_when_python_is_enabled(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_tauri=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("package.json", release_workflow)
        self.assertIn("package.json", tag_check_workflow)
        self.assertNotIn("pyproject.toml", release_workflow)
        self.assertNotIn("pyproject.toml", tag_check_workflow)

    def test_tauri_version_source_is_used_for_docker_release(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        docker_release_workflow = (
            destination / ".github/workflows/docker-release.yml"
        ).read_text()

        self.assertIn("package.json", docker_release_workflow)
        self.assertNotIn("Cargo.toml", docker_release_workflow)
        self.assertNotIn("cat version", docker_release_workflow)

    def test_quality_workflows_report_native_job_failure(self) -> None:
        configurations = {
            "python": (
                ("use_python=true",),
                ".github/workflows/pr-quality-checks.yml",
                "quality-checks",
                ("pytest", "mypy", "ruff-format", "ruff-check"),
            ),
            "rust": (
                ("use_python=false", "use_rust=true"),
                ".github/workflows/rust-quality-checks.yml",
                "rust-quality-checks",
                ("rustfmt", "clippy", "cargo-test"),
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                ".github/workflows/tauri-quality-checks.yml",
                "tauri-quality-checks",
                (
                    "eslint",
                    "prettier",
                    "typecheck",
                    "vitest",
                    "frontend-build",
                    "rustfmt",
                    "clippy",
                    "cargo-test",
                ),
            ),
            "chrome": (
                ("use_python=false", "use_chrome_extension=true"),
                ".github/workflows/chrome-extension-quality-checks.yml",
                "chrome-extension-quality-checks",
                ("eslint", "prettier", "typecheck", "vitest", "build"),
            ),
        }

        for name, (
            answers,
            workflow_path,
            job_id,
            quality_step_ids,
        ) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                workflow = (destination / workflow_path).read_text()
                self.assertNotIn(
                    f"  {job_id}:\n    continue-on-error: true\n",
                    workflow,
                )
                self.assertEqual(
                    workflow.count("continue-on-error: true"),
                    len(quality_step_ids),
                )
                for step_id in quality_step_ids:
                    self.assertIn(
                        f"        id: {step_id}\n"
                        "        continue-on-error: true\n",
                        workflow,
                    )
                    self.assertIn(f"steps.{step_id}.outcome", workflow)

                self.assertIn("if: ${{ !cancelled() }}", workflow)
                self.assertIn("has-failure=", workflow)
                self.assertIn("name: Enforce quality gate result", workflow)
                self.assertIn("exit 1", workflow)
                self.assertNotIn("checks: write", workflow)
                self.assertNotIn("actions/github-script", workflow)
                self.assertNotIn("github.rest.checks.create", workflow)

    def test_generated_workflows_pin_current_github_actions(self) -> None:
        configurations = {
            "python_release": (
                (
                    "use_python=true",
                    "use_gh_actions_release=true",
                    "use_gh_actions_pr_tag_check=true",
                ),
                {"pr-quality-checks.yml", "pr-tag-check.yml", "release.yml"},
            ),
            "docker_release": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                {"docker-release.yml"},
            ),
            "docker_quality": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_quality=true",
                ),
                {"docker-quality-checks.yml"},
            ),
            "rust": (
                ("use_python=false", "use_rust=true"),
                {"rust-quality-checks.yml"},
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                {"tauri-quality-checks.yml"},
            ),
            "chrome_release": (
                (
                    "use_python=false",
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                ),
                {
                    "chrome-extension-quality-checks.yml",
                    "chrome-extension-release.yml",
                },
            ),
        }

        for name, (answers, expected_workflows) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                workflow_directory = destination / ".github/workflows"
                self.assertEqual(
                    {workflow.name for workflow in workflow_directory.glob("*.yml")},
                    expected_workflows,
                )

                for workflow_name in expected_workflows:
                    workflow = (workflow_directory / workflow_name).read_text()
                    action_lines = [
                        line
                        for line in (
                            rendered_line.strip()
                            for rendered_line in workflow.splitlines()
                        )
                        if line.startswith("uses: ")
                    ]
                    self.assertTrue(action_lines, workflow_name)
                    for action_line in action_lines:
                        reference, separator, version_comment = action_line.partition(" # ")
                        self.assertTrue(separator, action_line)
                        self.assertRegex(
                            reference,
                            r"^uses: [^@]+@[0-9a-f]{40}$",
                            action_line,
                        )
                        self.assertRegex(version_comment, r"^v[0-9]", action_line)

    def test_template_quality_workflow_runs_template_tests(self) -> None:
        workflow = (
            REPO_ROOT / ".github/workflows/template-quality-checks.yml"
        ).read_text()
        self.assertIn("  template-quality-checks:", workflow)
        self.assertIn("enable-cache: false", workflow)
        self.assertIn("copier==9.17.1", workflow)
        self.assertIn("python -m unittest discover -s template_tests", workflow)

        result, destination = self.copy_template("use_python=false")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(
            (destination / ".github/workflows/template-quality-checks.yml").exists()
        )

    def test_readme_defines_required_quality_check_contract(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text()

        self.assertIn("PR quality workflowは必須quality gate", readme)
        for job_name in (
            "template-quality-checks",
            "quality-checks",
            "rust-quality-checks",
            "chrome-extension-quality-checks",
            "tauri-quality-checks",
            "docker-quality-checks",
        ):
            with self.subTest(job_name=job_name):
                self.assertIn(f"`{job_name}`", readme)
        self.assertNotIn("Advisory quality gate", readme)

    def test_dependabot_config_tracks_rendered_ecosystems_and_workflows(self) -> None:
        configurations = {
            "no_updates": (("use_python=false",), None),
            "python": (
                ("use_python=true",),
                (("uv", "/"), ("github-actions", "/")),
            ),
            "rust": (
                ("use_python=false", "use_rust=true"),
                (("cargo", "/"), ("github-actions", "/")),
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                (
                    ("cargo", "/src-tauri"),
                    ("npm", "/"),
                    ("github-actions", "/"),
                ),
            ),
            "chrome_extension": (
                ("use_python=false", "use_chrome_extension=true"),
                (("npm", "/"), ("github-actions", "/")),
            ),
            "chrome_extension_release_subdirectory": (
                (
                    "use_python=false",
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                    "chrome_extension_release_package_root_directory=extension",
                ),
                (
                    ("npm", "/"),
                    ("npm", "/extension"),
                    ("github-actions", "/"),
                ),
            ),
            "docker_without_workflow": (
                ("use_python=false", "use_docker=true"),
                (("docker", "/"),),
            ),
            "docker_dependabot_disabled": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_dependabot_docker=false",
                ),
                None,
            ),
            "docker_dependabot_disabled_with_python": (
                (
                    "use_python=true",
                    "use_docker=true",
                    "use_dependabot_docker=false",
                ),
                (("uv", "/"), ("github-actions", "/")),
            ),
            "docker_dependabot_disabled_with_docker_release": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_dependabot_docker=false",
                    "use_gh_actions_docker_release=true",
                ),
                (("github-actions", "/"),),
            ),
            "custom_workflow_only": (
                (
                    "use_python=false",
                    "use_dependabot_github_actions=true",
                ),
                (("github-actions", "/"),),
            ),
            "python_with_github_actions_dependabot_disabled": (
                (
                    "use_python=true",
                    "use_dependabot_github_actions=false",
                ),
                (("uv", "/"),),
            ),
            "custom_workflow_with_docker_dependabot_disabled": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_dependabot_docker=false",
                    "use_dependabot_github_actions=true",
                ),
                (("github-actions", "/"),),
            ),
            "docker_release": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                (("docker", "/"), ("github-actions", "/")),
            ),
            "release_workflow_only": (
                ("use_python=false", "use_gh_actions_release=true"),
                (("github-actions", "/"),),
            ),
        }

        for name, (answers, expected_updates) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                dependabot_config = destination / ".github/dependabot.yml"
                if expected_updates is None:
                    self.assertFalse(dependabot_config.exists())
                    continue

                self.assertEqual(
                    dependabot_config.read_text(),
                    self.expected_dependabot_config(*expected_updates),
                )

    def test_github_actions_dependabot_selection_survives_recopy(self) -> None:
        configurations = {
            "custom_workflow_enabled": (
                (
                    "use_python=false",
                    "use_dependabot_github_actions=true",
                ),
                True,
                (("github-actions", "/"),),
            ),
            "generated_workflow_disabled": (
                (
                    "use_python=true",
                    "use_dependabot_github_actions=false",
                ),
                False,
                (("uv", "/"),),
            ),
        }

        for name, (answers, selected, expected_updates) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                answers_file = destination / ".copier-answers.yml"
                self.assertIn(
                    f"use_dependabot_github_actions: {str(selected).lower()}",
                    answers_file.read_text(),
                )

                for _ in range(2):
                    recopy_result = self.recopy_template(destination)
                    self.assertEqual(recopy_result.returncode, 0, recopy_result.stdout)
                    self.assertEqual(
                        (destination / ".github/dependabot.yml").read_text(),
                        self.expected_dependabot_config(*expected_updates),
                    )

    def test_docker_dependabot_opt_out_survives_recopy(self) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        dependabot_config = destination / ".github/dependabot.yml"
        self.assertTrue(dependabot_config.exists())

        answers_file = destination / ".copier-answers.yml"
        answers = answers_file.read_text()
        self.assertIn("use_dependabot_docker: true", answers)
        answers_file.write_text(
            answers.replace(
                "use_dependabot_docker: true",
                "use_dependabot_docker: false",
            )
        )
        dependabot_config.unlink()

        recopy_result = self.recopy_template(destination)

        self.assertEqual(recopy_result.returncode, 0, recopy_result.stdout)
        self.assertFalse(dependabot_config.exists())

        second_recopy_result = self.recopy_template(destination)

        self.assertEqual(second_recopy_result.returncode, 0, second_recopy_result.stdout)
        self.assertFalse(dependabot_config.exists())

    def test_docker_dependabot_opt_out_updates_other_ecosystems_on_recopy(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_docker=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        answers_file = destination / ".copier-answers.yml"
        answers = answers_file.read_text()
        self.assertIn("use_dependabot_docker: true", answers)
        answers_file.write_text(
            answers.replace(
                "use_dependabot_docker: true",
                "use_dependabot_docker: false",
            )
        )

        recopy_result = self.recopy_template(destination)

        self.assertEqual(recopy_result.returncode, 0, recopy_result.stdout)
        self.assertEqual(
            (destination / ".github/dependabot.yml").read_text(),
            self.expected_dependabot_config(
                ("uv", "/"),
                ("github-actions", "/"),
            ),
        )

    def test_tauri_eslint_config_allows_node_globals_in_config_files(self) -> None:
        result, destination = self.copy_template("use_tauri=true")

        self.assertEqual(result.returncode, 0, result.stdout)

        eslint_config = (destination / "eslint.config.mjs").read_text()

        self.assertIn('files: ["vite.config.ts", "vitest.config.ts"]', eslint_config)
        self.assertIn("...globals.node", eslint_config)
