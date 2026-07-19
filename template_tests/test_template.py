import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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

    def test_agent_guidance_requires_issue_first_branch_workflow(self) -> None:
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

        for name, answers in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                agents_guidance = (destination / "AGENTS.md").read_text()
                claude_guidance = (destination / "CLAUDE.md").read_text()
                self.assertEqual(agents_guidance, claude_guidance)
                for rule in required_rules:
                    self.assertIn(rule, agents_guidance)

    def test_agent_workflow_docs_are_generated_and_linked(self) -> None:
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

    def test_release_workflows_are_rerunnable_after_partial_failure(self) -> None:
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
        }

        for name, (answers, workflow_name) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                workflow = (
                    destination / ".github/workflows" / workflow_name
                ).read_text()
                if workflow_name == "docker-release.yml":
                    self.assertIn(
                        "      - name: Check version tag\n        id: tag",
                        workflow,
                    )
                    self.assertIn(
                        'echo "exists=true" >> "$GITHUB_OUTPUT"',
                        workflow,
                    )
                    self.assertIn(
                        'echo "exists=false" >> "$GITHUB_OUTPUT"',
                        workflow,
                    )
                    for step_name in (
                        "Set up Docker Buildx",
                        "Login to Docker Hub",
                        "Build and push",
                    ):
                        self.assertIn(
                            f"      - name: {step_name}\n"
                            "        if: steps.tag.outputs.exists != 'true'",
                            workflow,
                        )
                    self.assertLess(
                        workflow.index("      - name: Check version tag"),
                        workflow.index("      - name: Build and push"),
                    )
                    self.assertLess(
                        workflow.index("      - name: Build and push"),
                        workflow.index("      - name: Create or reuse version tag"),
                    )

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

                tag_script = self.workflow_step_script(
                    destination,
                    workflow_name,
                    "Create or reuse version tag",
                )
                tag_env = {**os.environ, "TAG": "0.1.0"}

                first_tag_result = self.run_process(
                    ["bash"],
                    destination,
                    env=tag_env,
                    script=tag_script,
                )
                self.assertEqual(
                    first_tag_result.returncode,
                    0,
                    first_tag_result.stdout,
                )

                rerun_tag_result = self.run_process(
                    ["bash"],
                    destination,
                    env=tag_env,
                    script=tag_script,
                )
                self.assertEqual(
                    rerun_tag_result.returncode,
                    0,
                    rerun_tag_result.stdout,
                )
                self.assertIn(
                    "already points to this release commit; reusing it",
                    rerun_tag_result.stdout,
                )

                fake_bin = destination.parent / "bin"
                fake_bin.mkdir()
                fake_gh = fake_bin / "gh"
                fake_gh.write_text(
                    "#!/bin/sh\n"
                    'printf "%s\\n" "$*" >> "$GH_LOG"\n'
                    'case "$1 $2" in\n'
                    '  "release view") [ -f "$GH_RELEASE_STATE" ] ;;\n'
                    '  "release create") : > "$GH_RELEASE_STATE" ;;\n'
                    "  *) exit 1 ;;\n"
                    "esac\n"
                )
                fake_gh.chmod(0o755)
                gh_log = destination.parent / "gh.log"
                release_state = destination.parent / "release-exists"
                release_script = self.workflow_step_script(
                    destination,
                    workflow_name,
                    "Create or reuse GitHub Release",
                ).replace(
                    "${{ github.server_url }}",
                    "https://github.example",
                ).replace(
                    "${{ github.repository }}",
                    "owner/project",
                )
                release_env = {
                    **os.environ,
                    "GH_LOG": str(gh_log),
                    "GH_RELEASE_STATE": str(release_state),
                    "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                    "TAG": "0.1.0",
                }

                for run_number in (1, 2):
                    release_result = self.run_process(
                        ["bash"],
                        destination,
                        env=release_env,
                        script=release_script,
                    )
                    self.assertEqual(
                        release_result.returncode,
                        0,
                        f"release run {run_number}: {release_result.stdout}",
                    )
                self.assertEqual(gh_log.read_text().count("release create"), 1)

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
                    env=tag_env,
                    script=tag_script,
                )
                self.assertNotEqual(foreign_commit_result.returncode, 0)
                self.assertIn(
                    "already points to",
                    foreign_commit_result.stdout,
                )

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

        for version in ("1.2.3+build.1", "a" * 129):
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
            "          VERSION: ${{ steps.version.outputs.version }}\n"
            "        run: |\n"
            "          git fetch --tags",
            workflow,
        )
        self.assertIn(
            'git show-ref --tags --verify --quiet "refs/tags/$VERSION"',
            workflow,
        )

        for step_name in ("Check if tag exists", "Build version tag check summary"):
            script = self.workflow_step_script(
                destination,
                "pr-tag-check.yml",
                step_name,
            )
            self.assertNotIn("steps.version.outputs.version", script)

    def test_long_chrome_extension_name_is_already_formatted(self) -> None:
        long_name = "Very Long Chrome Extension Name For Formatting"

        result, destination = self.copy_template(
            "use_chrome_extension=true",
            f"chrome_extension_name={long_name}",
        )
        self.assertEqual(result.returncode, 0, result.stdout)

        install_result = subprocess.run(
            ["npm", "install", "--prefix", str(destination)],
            check=False,
            env={**os.environ, "npm_config_cache": "/private/tmp/codex-npm-cache"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.assertEqual(install_result.returncode, 0, install_result.stdout)

        format_result = subprocess.run(
            ["npm", "--prefix", str(destination), "run", "format:check"],
            check=False,
            env={**os.environ, "npm_config_cache": "/private/tmp/codex-npm-cache"},
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

    def test_existing_chrome_extension_is_standardized_and_recopies(self) -> None:
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
        self.assertIn("Chrome extension version source validation failed", workflow)
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
                self.assertNotIn('checkConclusion = "neutral";', workflow)
                self.assertIn(
                    "name: Enforce version tag availability",
                    workflow,
                )
                self.assertIn(
                    "if: always() && steps.tag.outputs.exists != 'false'",
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
        self.assertIn("gh release view", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("--clobber", workflow)
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

    def test_generated_workflows_use_current_github_action_versions(self) -> None:
        checkout = "actions/checkout@v7"
        setup_python = "actions/setup-python@v6"
        github_script = "actions/github-script@v9"
        configurations = {
            "python_release": (
                (
                    "use_python=true",
                    "use_gh_actions_release=true",
                    "use_gh_actions_pr_tag_check=true",
                ),
                {
                    "pr-quality-checks.yml": {checkout, setup_python},
                    "pr-tag-check.yml": {checkout, github_script},
                    "release.yml": {checkout},
                },
            ),
            "docker_release": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                {"docker-release.yml": {checkout}},
            ),
            "rust": (
                ("use_python=false", "use_rust=true"),
                {"rust-quality-checks.yml": {checkout}},
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                {"tauri-quality-checks.yml": {checkout}},
            ),
            "chrome_release": (
                (
                    "use_python=false",
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                ),
                {
                    "chrome-extension-quality-checks.yml": {checkout},
                    "chrome-extension-release.yml": {checkout},
                },
            ),
        }
        target_actions = {
            reference.partition("@")[0]
            for reference in (checkout, setup_python, github_script)
        }

        for name, (answers, expected_workflows) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                workflow_directory = destination / ".github/workflows"
                self.assertEqual(
                    {workflow.name for workflow in workflow_directory.glob("*.yml")},
                    set(expected_workflows),
                )

                for workflow_name, expected_references in expected_workflows.items():
                    workflow = (workflow_directory / workflow_name).read_text()
                    action_references = {
                        line.removeprefix("uses: ")
                        for line in (
                            rendered_line.strip()
                            for rendered_line in workflow.splitlines()
                        )
                        if line.startswith("uses: actions/")
                    }
                    self.assertEqual(
                        {
                            reference
                            for reference in action_references
                            if reference.partition("@")[0] in target_actions
                        },
                        expected_references,
                    )

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
