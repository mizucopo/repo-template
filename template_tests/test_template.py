import glob
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from enum import Enum
from pathlib import Path
from typing import NamedTuple

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
NPM_CACHE = Path(tempfile.gettempdir()) / "repo-template-npm-cache"


class ManagedStarter(NamedTuple):
    template_path: str
    project_path: str
    marker: str


class ProjectFileState(Enum):
    CUSTOMIZED = "customized"
    DELETED = "deleted"

    def apply(self, path: Path, customized_content: bytes) -> bytes | None:
        if self is ProjectFileState.CUSTOMIZED:
            path.write_bytes(customized_content)
            return customized_content
        path.unlink()
        return None


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

    def create_versioned_template(
        self,
        starter_path: str,
        starter_content: str,
    ) -> tuple[Path, Path]:
        template = self.copy_template_repository()
        starter = template / starter_path
        starter.write_text(starter_content)
        self.commit_repository(template, "template v1")
        return template, starter

    def copy_template_repository(self) -> Path:
        template_root = tempfile.TemporaryDirectory()
        self.addCleanup(template_root.cleanup)

        template = Path(template_root.name).resolve() / "template"
        shutil.copytree(
            REPO_ROOT,
            template,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
        return template

    def copy_versioned_template(
        self,
        template: Path,
        destination: Path,
        *answers: str,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "copier",
            "copy",
            "--trust",
            "--defaults",
            "--vcs-ref",
            "HEAD",
        ]
        for answer in answers:
            command.extend(["-d", answer])
        command.extend([str(template), str(destination)])
        return self.run_process(command, destination.parent)

    def create_versioned_project(
        self,
        template: Path,
        *answers: str,
    ) -> Path:
        project_root = tempfile.TemporaryDirectory()
        self.addCleanup(project_root.cleanup)
        project = Path(project_root.name).resolve() / "project"

        copied = self.copy_versioned_template(template, project, *answers)
        self.assertEqual(copied.returncode, 0, copied.stdout)
        self.commit_repository(project, "initial template copy")
        return project


    def create_tauri_project_with_branding_asset_state(
        self,
        template: Path,
        project_state: ProjectFileState,
        customized_branding: bytes,
    ) -> tuple[Path, Path, bytes | None]:
        project = self.create_versioned_project(
            template,
            "use_python=false",
            "use_tauri=true",
        )
        project_icon = project / "src-tauri/icons/icon.png"
        expected_branding = project_state.apply(project_icon, customized_branding)
        self.commit_repository(project, f"project branding {project_state.value}")
        return project, project_icon, expected_branding

    def assert_project_owned_branding_asset_survives_codebase_update(
        self,
        template: Path,
        project: Path,
        project_icon: Path,
        expected_branding: bytes | None,
    ) -> None:
        template_icon = (
            template / "{% if use_tauri %}src-tauri{% endif %}/icons/icon.png"
        )
        template_icon.write_bytes(b"template branding")
        self.commit_repository(template, "template branding")

        updated = self.update_versioned_project(project)

        self.assertEqual(updated.returncode, 0, updated.stdout)
        actual_branding = project_icon.read_bytes() if project_icon.exists() else None
        self.assertEqual(actual_branding, expected_branding)

    def update_versioned_project(
        self,
        destination: Path,
        *answers: str,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "copier",
            "update",
            "--trust",
            "--defaults",
            "--vcs-ref",
            "HEAD",
        ]
        for answer in answers:
            command.extend(["-d", answer])
        command.append(str(destination))
        return self.run_process(command, destination.parent)

    def commit_repository(self, repository: Path, message: str) -> None:
        if not (repository / ".git").exists():
            init = self.run_process(["git", "init", "-b", "main"], repository)
            self.assertEqual(init.returncode, 0, init.stdout)
            for key, value in (
                ("user.name", "Template Test"),
                ("user.email", "template-test@example.com"),
            ):
                config = self.run_process(
                    ["git", "config", key, value],
                    repository,
                )
                self.assertEqual(config.returncode, 0, config.stdout)

        add = self.run_process(["git", "add", "."], repository)
        self.assertEqual(add.returncode, 0, add.stdout)
        commit = self.run_process(["git", "commit", "-m", message], repository)
        self.assertEqual(commit.returncode, 0, commit.stdout)

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


    def test_answers_file_does_not_record_legacy_starter_ownership(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_rust=true",
            "use_chrome_extension=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        answers = (destination / ".copier-answers.yml").read_text()
        self.assertNotIn("repo_template_python_starters_created", answers)
        self.assertNotIn("repo_template_rust_starters_created", answers)
        self.assertNotIn("repo_template_chrome_starters_created", answers)
        self.assertNotIn("repo_template_tauri_starters_created", answers)

    def test_answers_file_records_project_owned_branding_asset_history(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_tauri=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        answers = (destination / ".copier-answers.yml").read_text()
        self.assertIn("repo_template_tauri_branding_assets_created: true", answers)

    def test_python_application_is_not_installed_as_a_package(self) -> None:
        result, destination = self.copy_template("use_python=true")

        self.assertEqual(result.returncode, 0, result.stdout)
        pyproject = (destination / "pyproject.toml").read_text()
        self.assertIn("[tool.uv]\npackage = false", pyproject)
        self.assertTrue((destination / "src/.gitkeep").is_file())
        self.assertFalse((destination / "src/__init__.py").exists())

    def test_version_management_defaults_to_enabled(self) -> None:
        result, destination = self.copy_template("use_python=false")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((destination / "version").read_text(), "0.1.0\n")
        answers = (destination / ".copier-answers.yml").read_text()
        self.assertIn("use_version_management: true", answers)
        self.assertIn("project_version: 0.1.0", answers)

    def test_version_management_can_be_disabled_for_versionless_project(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_version_management=false",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((destination / "version").exists())
        self.assertFalse((destination / ".github/workflows/release.yml").exists())
        self.assertFalse(
            (destination / ".github/workflows/docker-release.yml").exists()
        )
        self.assertFalse(
            (destination / ".github/workflows/release-classification.yml").exists()
        )
        answers = (destination / ".copier-answers.yml").read_text()
        self.assertIn("use_version_management: false", answers)
        for inactive_answer in (
            "project_version",
            "use_gh_actions_release",
            "use_gh_actions_docker_release",
            "use_gh_actions_chrome_extension_release",
            "use_gh_actions_merge_preparation",
        ):
            with self.subTest(answer=inactive_answer):
                self.assertNotIn(f"{inactive_answer}:", answers)

    def test_version_management_can_be_disabled_with_docker_quality(self) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_version_management=false",
            "use_docker=true",
            "use_gh_actions_docker_quality=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((destination / "version").exists())
        self.assertTrue(
            (destination / ".github/workflows/docker-quality-checks.yml").is_file()
        )
        self.assertFalse(
            (destination / ".github/workflows/docker-release.yml").exists()
        )

    def test_version_management_is_required_by_runtime_support(self) -> None:
        for runtime_answer in (
            "use_python",
            "use_rust",
            "use_chrome_extension",
            "use_tauri",
        ):
            with self.subTest(runtime=runtime_answer):
                result, _ = self.copy_template(
                    "use_version_management=false",
                    f"{runtime_answer}=true",
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    f"{runtime_answer}=true の場合は "
                    "use_version_management=true が必要です。",
                    result.stdout,
                )

    def test_version_management_rejects_explicit_version_features(self) -> None:
        configurations = {
            "project_version": (
                "project_version=1.2.3",
                "project_versionを指定する場合",
            ),
            "release": (
                "use_gh_actions_release=true",
                "use_gh_actions_release=true の場合",
            ),
            "docker_release": (
                "use_docker=true",
                "use_gh_actions_docker_release=true",
                "use_gh_actions_docker_release=true の場合",
            ),
            "chrome_extension_release": (
                "use_gh_actions_chrome_extension_release=true",
                "use_gh_actions_chrome_extension_release=true の場合",
            ),
        }

        for name, values in configurations.items():
            *answers, expected_error = values
            with self.subTest(name=name):
                result, _ = self.copy_template(
                    "use_python=false",
                    "use_version_management=false",
                    *answers,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stdout)

    def test_disabling_version_management_removes_generated_files_and_answers(
        self,
    ) -> None:
        configurations = {
            "release": (
                (
                    "use_python=false",
                    "use_gh_actions_release=true",
                ),
                (
                    "version",
                    ".github/workflows/release.yml",
                    ".github/workflows/release-classification.yml",
                    ".github/scripts/release.py",
                ),
            ),
            "docker_release": (
                (
                    "use_python=false",
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                ),
                (
                    "version",
                    ".github/workflows/docker-release.yml",
                    ".github/workflows/release-classification.yml",
                    ".github/scripts/release.py",
                    ".github/scripts/manage-docker-image-owner.sh",
                ),
            ),
        }

        for name, (answers, generated_paths) in configurations.items():
            with self.subTest(name=name):
                template = self.copy_template_repository()
                self.commit_repository(template, "versioned template")
                project = self.create_versioned_project(template, *answers)

                for relative_path in generated_paths:
                    self.assertTrue(
                        (project / relative_path).is_file(),
                        relative_path,
                    )

                updated = self.update_versioned_project(
                    project,
                    "use_version_management=false",
                )

                self.assertEqual(updated.returncode, 0, updated.stdout)
                for relative_path in generated_paths:
                    self.assertFalse(
                        (project / relative_path).exists(),
                        relative_path,
                    )

                project_answers = (project / ".copier-answers.yml").read_text()
                self.assertIn("use_version_management: false", project_answers)
                for inactive_answer in (
                    "project_version",
                    "use_gh_actions_release",
                    "use_gh_actions_docker_release",
                    "use_gh_actions_chrome_extension_release",
                    "use_gh_actions_merge_preparation",
                ):
                    self.assertNotIn(f"{inactive_answer}:", project_answers)

    def test_project_metadata_and_python_project_kinds_are_rendered(self) -> None:
        cases = {
            "application": ("application", "src/.gitkeep", "package = false"),
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
            'src tests stubs && mypy && python tests/run_pytest.py"',
            pyproject,
        )
        self.assertIn(
            'fix = "ruff check --fix src tests stubs && ruff format src tests stubs"',
            pyproject,
        )
        self.assertIn('test = "task check"', pyproject)

    def test_generated_python_projects_pass_local_quality_gate(self) -> None:
        for python_version in ("3.13", "3.14"):
            for kind in ("application", "package", "library"):
                with self.subTest(python_version=python_version, kind=kind):
                    result, destination = self.copy_template(
                        "use_python=true",
                        f"python_version={python_version}",
                        f"python_project_kind={kind}",
                        "use_version_management=true",
                        "use_gh_actions_release=true",
                    )
                    self.assertEqual(result.returncode, 0, result.stdout)
                    self.assertTrue(
                        (destination / ".github/scripts/release.py").is_file()
                    )
                    synced = self.run_process(["uv", "sync"], destination)
                    self.assertEqual(synced.returncode, 0, synced.stdout)
                    checked = self.run_process(["uv", "run", "task", "check"], destination)
                    self.assertEqual(checked.returncode, 0, checked.stdout)
                    for command in (
                        ["uv", "run", "ruff", "check", ".github/scripts/release.py"],
                        [
                            "uv",
                            "run",
                            "ruff",
                            "format",
                            "--check",
                            ".github/scripts/release.py",
                        ],
                        [
                            "uv",
                            "run",
                            "python",
                            "-m",
                            "py_compile",
                            ".github/scripts/release.py",
                        ],
                    ):
                        with self.subTest(command=command):
                            checked = self.run_process(command, destination)
                            self.assertEqual(checked.returncode, 0, checked.stdout)

    def test_python_application_quality_gate_supports_flat_imports(self) -> None:
        result, destination = self.copy_template("use_python=true")
        self.assertEqual(result.returncode, 0, result.stdout)

        synced = self.run_process(["uv", "sync"], destination)
        self.assertEqual(synced.returncode, 0, synced.stdout)

        empty_check = self.run_process(["uv", "run", "task", "check"], destination)
        self.assertEqual(empty_check.returncode, 0, empty_check.stdout)
        self.assertIn(
            "No user test files or items collected; skipping test execution.",
            empty_check.stdout,
        )

        (destination / "src/config.py").write_text("VALUE = 1\n")
        test_path = destination / "tests/test_config.py"
        test_path.write_text(
            "from config import VALUE\n\n\n"
            "def test_config() -> None:\n"
            "    assert VALUE == 1\n"
        )
        checked = self.run_process(["uv", "run", "task", "check"], destination)
        self.assertEqual(checked.returncode, 0, checked.stdout)

        unimported_source = destination / "src/unimported.py"
        unimported_source.write_text('value: int = "wrong type"\n')
        type_error = self.run_process(["uv", "run", "mypy"], destination)
        self.assertEqual(type_error.returncode, 1, type_error.stdout)
        self.assertIn("src/unimported.py", type_error.stdout)
        unimported_source.unlink()

        test_path.write_text(test_path.read_text().replace("VALUE == 1", "VALUE == 999"))
        failed = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "--tb=short", "-q"],
            destination,
        )
        self.assertEqual(failed.returncode, 1, failed.stdout)

        deselected = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "-k", "not_selected"],
            destination,
        )
        self.assertEqual(deselected.returncode, 5, deselected.stdout)
        invalid_option = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "--not-a-pytest-option"],
            destination,
        )
        self.assertEqual(invalid_option.returncode, 4, invalid_option.stdout)

        test_path.write_text("raise RuntimeError('collection failure')\n")
        collection_error = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "--tb=short", "-q"],
            destination,
        )
        self.assertEqual(collection_error.returncode, 2, collection_error.stdout)

        test_path.write_text("")
        no_tests_collected = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "-q"],
            destination,
        )
        self.assertEqual(no_tests_collected.returncode, 5, no_tests_collected.stdout)

    def test_python_application_pytest_runner_honors_collection_configuration(
        self,
    ) -> None:
        result, destination = self.copy_template("use_python=true")
        self.assertEqual(result.returncode, 0, result.stdout)
        synced = self.run_process(["uv", "sync"], destination)
        self.assertEqual(synced.returncode, 0, synced.stdout)

        pyproject = destination / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text().replace(
                'testpaths = ["tests"]',
                'testpaths = ["specs"]\npython_files = ["spec_*.py"]',
            )
        )
        specs = destination / "specs"
        specs.mkdir()
        spec = specs / "spec_example.py"
        spec.write_text("def test_example() -> None:\n    assert False\n")
        failed = self.run_process(["uv", "run", "task", "check"], destination)
        self.assertEqual(failed.returncode, 1, failed.stdout)
        self.assertIn("spec_example.py", failed.stdout)

        spec.write_text("")
        empty_file = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "-q"], destination
        )
        self.assertEqual(empty_file.returncode, 5, empty_file.stdout)

    def test_python_application_pytest_runner_honors_collector_plugins(self) -> None:
        result, destination = self.copy_template("use_python=true")
        self.assertEqual(result.returncode, 0, result.stdout)
        synced = self.run_process(["uv", "sync"], destination)
        self.assertEqual(synced.returncode, 0, synced.stdout)

        (destination / "tests/conftest.py").write_text(
            "import pytest\n\n\n"
            "class CaseItem(pytest.Item):\n"
            "    def runtest(self):\n"
            "        raise AssertionError('custom collector failure')\n\n\n"
            "class CaseFile(pytest.File):\n"
            "    def collect(self):\n"
            "        yield CaseItem.from_parent(self, name=self.path.name)\n\n\n"
            "def pytest_collect_file(file_path, parent):\n"
            "    if file_path.suffix == '.case':\n"
            "        return CaseFile.from_parent(parent, path=file_path)\n"
        )
        (destination / "tests/example.case").write_text("custom test\n")
        failed = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "--tb=short", "-q"],
            destination,
        )
        self.assertEqual(failed.returncode, 1, failed.stdout)
        self.assertIn("custom collector failure", failed.stdout)

        conftest = destination / "tests/conftest.py"
        conftest.write_text(
            conftest.read_text()
            .replace(
                "yield CaseItem.from_parent(self, name=self.path.name)", "return []"
            )
            .replace(
                "def pytest_collect_file(file_path, parent):\n"
                "    if file_path.suffix == '.case':\n"
                "        return CaseFile.from_parent(parent, path=file_path)\n",
                "@pytest.hookimpl(wrapper=True)\n"
                "def pytest_collect_file(file_path, parent):\n"
                "    collectors = yield\n"
                "    if file_path.suffix == '.case':\n"
                "        collectors.append(CaseFile.from_parent(parent, path=file_path))\n"
                "    return collectors\n",
            )
        )
        empty_custom_file = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "-q"], destination
        )
        self.assertEqual(empty_custom_file.returncode, 5, empty_custom_file.stdout)

    def test_python_application_pytest_runner_tracks_directory_collectors(self) -> None:
        result, destination = self.copy_template("use_python=true")
        self.assertEqual(result.returncode, 0, result.stdout)
        synced = self.run_process(["uv", "sync"], destination)
        self.assertEqual(synced.returncode, 0, synced.stdout)

        (destination / "tests/cases").mkdir()
        (destination / "tests/conftest.py").write_text(
            "import pytest\n\n\n"
            "class CaseItem(pytest.Item):\n"
            "    def runtest(self):\n"
            "        pass\n\n\n"
            "class CaseDirectory(pytest.Directory):\n"
            "    def collect(self):\n"
            "        yield CaseItem.from_parent(self, name='generated')\n\n\n"
            "def pytest_collect_directory(path, parent):\n"
            "    if path.name == 'cases':\n"
            "        return CaseDirectory.from_parent(parent, path=path)\n"
        )
        deselected = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "-k", "not_selected"],
            destination,
        )
        self.assertEqual(deselected.returncode, 5, deselected.stdout)

    def test_python_application_pytest_runner_tracks_plugin_deselection(self) -> None:
        result, destination = self.copy_template("use_python=true")
        self.assertEqual(result.returncode, 0, result.stdout)
        synced = self.run_process(["uv", "sync"], destination)
        self.assertEqual(synced.returncode, 0, synced.stdout)

        (destination / "tests/conftest.py").write_text(
            "import pytest\n\n\n"
            "class GeneratedItem(pytest.Item):\n"
            "    def runtest(self):\n"
            "        pass\n\n\n"
            "def pytest_collection_modifyitems(session, config, items):\n"
            "    items.append(GeneratedItem.from_parent(session, name='generated'))\n"
            "    deselected = list(items)\n"
            "    items.clear()\n"
            "    config.hook.pytest_deselected(items=deselected)\n"
        )
        deselected = self.run_process(
            ["uv", "run", "python", "tests/run_pytest.py", "-q"], destination
        )
        self.assertEqual(deselected.returncode, 5, deselected.stdout)

    def test_python_application_pytest_runner_ignores_scaffold_collection(self) -> None:
        for testpaths in ("tests", "."):
            with self.subTest(testpaths=testpaths):
                result, destination = self.copy_template("use_python=true")
                self.assertEqual(result.returncode, 0, result.stdout)
                synced = self.run_process(["uv", "sync"], destination)
                self.assertEqual(synced.returncode, 0, synced.stdout)

                pyproject = destination / "pyproject.toml"
                pyproject.write_text(
                    pyproject.read_text().replace(
                        'testpaths = ["tests"]',
                        f'testpaths = ["{testpaths}"]\npython_files = ["*.py"]',
                    )
                )
                empty_check = self.run_process(
                    ["uv", "run", "task", "check"], destination
                )
                self.assertEqual(empty_check.returncode, 0, empty_check.stdout)

                initializer = destination / "tests/__init__.py"
                initializer.write_text("# Project-owned initializer\n")
                modified_initializer = self.run_process(
                    ["uv", "run", "python", "tests/run_pytest.py", "-q"],
                    destination,
                )
                self.assertEqual(
                    modified_initializer.returncode, 5, modified_initializer.stdout
                )
                initializer.write_text("")

                if testpaths == ".":
                    stub_initializer = destination / "stubs/__init__.py"
                    stub_initializer.write_text("# Project-owned stubs\n")
                    modified_stubs = self.run_process(
                        ["uv", "run", "python", "tests/run_pytest.py", "-q"],
                        destination,
                    )
                    self.assertEqual(modified_stubs.returncode, 5, modified_stubs.stdout)
                    stub_initializer.write_text("")

                user_test = destination / "tests/example.py"
                user_test.write_text("")
                empty_user_file = self.run_process(
                    ["uv", "run", "python", "tests/run_pytest.py", "-q"],
                    destination,
                )
                self.assertEqual(
                    empty_user_file.returncode, 5, empty_user_file.stdout
                )
                user_test.write_text("def test_example() -> None:\n    assert False\n")
                failed = self.run_process(
                    ["uv", "run", "python", "tests/run_pytest.py", "--tb=short", "-q"],
                    destination,
                )
                self.assertEqual(failed.returncode, 1, failed.stdout)

                user_test.unlink()
                runner = destination / "tests/run_pytest.py"
                runner.write_text(
                    runner.read_text()
                    + "\n\ndef test_runner_addition() -> None:\n"
                    + "    raise AssertionError('runner test detected')\n"
                )
                runner_test = self.run_process(
                    ["uv", "run", "python", "tests/run_pytest.py", "--tb=short", "-q"],
                    destination,
                )
                self.assertEqual(runner_test.returncode, 1, runner_test.stdout)
                self.assertIn("runner test detected", runner_test.stdout)

    def test_python_package_initializer_is_empty(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "python_project_kind=library",
            "project_name=sample-library",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        source = (destination / "src/sample_library/__init__.py").read_text()
        self.assertEqual(source, "")

    def test_python_import_smoke_test_is_only_generated_for_packages(self) -> None:
        cases = {
            "application": ("application", None),
            "package": ("package", "sample_project"),
            "library": ("library", "sample_project"),
        }

        for name, (kind, module_name) in cases.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(
                    "use_python=true",
                    f"python_project_kind={kind}",
                    "project_name=sample-project",
                )

                self.assertEqual(result.returncode, 0, result.stdout)
                smoke_test_path = destination / "tests/test_import.py"
                workflow = (
                    destination / ".github/workflows/pr-quality-checks.yml"
                ).read_text()
                if kind == "application":
                    self.assertFalse(smoke_test_path.exists())
                    self.assertIn(
                        "uv run task check", workflow
                    )
                else:
                    smoke_test = smoke_test_path.read_text()
                    self.assertIn(f'module_name = "{module_name}"', smoke_test)
                    self.assertFalse((destination / "tests/run_pytest.py").exists())
                    self.assertIn("uv run task check", workflow)

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


    def test_copier_update_merges_template_and_project_code_changes(self) -> None:
        cases = {
            "python": (
                ("use_python=true", "python_project_kind=package"),
                "{% if use_python and python_project_kind != 'application' %}src{% endif %}/{{ python_package_name }}/__init__.py",
                "src/test_project/__init__.py",
                "#",
            ),
            "rust": (
                ("use_python=false", "use_rust=true"),
                "{% if use_rust %}src{% endif %}/main.rs",
                "src/main.rs",
                "//",
            ),
            "chrome": (
                ("use_python=false", "use_chrome_extension=true"),
                "{% if use_chrome_extension %}src{% endif %}/background.ts.jinja",
                "src/background.ts",
                "//",
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                "{% if use_tauri %}src{% endif %}/main.ts",
                "src/main.ts",
                "//",
            ),
        }

        for name, (answers, template_path, project_path, comment) in cases.items():
            with self.subTest(name=name):
                template_v1_content = (
                    f"{comment} template-standard-v1\n"
                    f"{comment} shared-seam\n"
                    f"{comment} project-hook\n"
                )
                template, template_starter = self.create_versioned_template(
                    template_path,
                    template_v1_content,
                )
                project = self.create_versioned_project(template, *answers)
                project_starter = project / project_path
                project_starter.write_text(
                    project_starter.read_text().replace(
                        "project-hook",
                        "project-customization",
                    )
                )
                project_owned = project / "project-owned.txt"
                project_owned.write_text("keep me\n")
                self.commit_repository(project, "project customization")

                template_starter.write_text(
                    template_starter.read_text().replace(
                        "template-standard-v1",
                        "template-standard-v2",
                    )
                )
                self.commit_repository(template, "template v2")

                updated = self.update_versioned_project(project)

                self.assertEqual(updated.returncode, 0, updated.stdout)
                merged = project_starter.read_text()
                self.assertIn("template-standard-v2", merged)
                self.assertIn("project-customization", merged)
                self.assertNotIn("<<<<<<<", merged)
                self.assertEqual(project_owned.read_text(), "keep me\n")

    def test_copier_update_propagates_every_managed_starter(self) -> None:
        cases = {
            "python_application": (
                ("use_python=true",),
                (
                    ManagedStarter(
                        "{% if use_python and python_project_kind == 'application' %}src{% endif %}/.gitkeep",
                        "src/.gitkeep",
                        "# template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_python %}stubs{% endif %}/__init__.py",
                        "stubs/__init__.py",
                        "# template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_python %}tests{% endif %}/__init__.py",
                        "tests/__init__.py",
                        "# template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_python and python_project_kind == 'application' %}tests{% endif %}/run_pytest.py",
                        "tests/run_pytest.py",
                        "# template-code-update",
                    ),
                ),
            ),
            "python_package": (
                (
                    "use_python=true",
                    "python_project_kind=package",
                    "project_name=sample-project",
                ),
                (
                    ManagedStarter(
                        "{% if use_python and python_project_kind != 'application' %}src{% endif %}/{{ python_package_name }}/__init__.py",
                        "src/sample_project/__init__.py",
                        "# template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_python and python_project_kind != 'application' %}tests{% endif %}/test_import.py.jinja",
                        "tests/test_import.py",
                        "# template-code-update",
                    ),
                ),
            ),
            "rust": (
                ("use_python=false", "use_rust=true"),
                (
                    ManagedStarter(
                        "{% if use_rust %}src{% endif %}/main.rs",
                        "src/main.rs",
                        "// template-code-update",
                    ),
                ),
            ),
            "chrome": (
                ("use_python=false", "use_chrome_extension=true"),
                (
                    ManagedStarter(
                        "{% if use_chrome_extension %}src{% endif %}/background.ts.jinja",
                        "src/background.ts",
                        "// template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_chrome_extension %}src{% endif %}/lib/extension-title.ts",
                        "src/lib/extension-title.ts",
                        "// template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_chrome_extension %}src{% endif %}/popup.css",
                        "src/popup.css",
                        "/* template-code-update */",
                    ),
                    ManagedStarter(
                        "{% if use_chrome_extension %}src{% endif %}/popup.html.jinja",
                        "src/popup.html",
                        "<!-- template-code-update -->",
                    ),
                    ManagedStarter(
                        "{% if use_chrome_extension %}src{% endif %}/popup.ts.jinja",
                        "src/popup.ts",
                        "// template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_chrome_extension %}tests{% endif %}/lib/extension-title.test.ts",
                        "tests/lib/extension-title.test.ts",
                        "// template-code-update",
                    ),
                ),
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                (
                    ManagedStarter(
                        "{% if use_tauri %}index.html{% endif %}.jinja",
                        "index.html",
                        "<!-- template-code-update -->",
                    ),
                    ManagedStarter(
                        "{% if use_tauri %}src{% endif %}/lib/greeting.ts",
                        "src/lib/greeting.ts",
                        "// template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_tauri %}src{% endif %}/main.ts",
                        "src/main.ts",
                        "// template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_tauri %}src{% endif %}/styles.css",
                        "src/styles.css",
                        "/* template-code-update */",
                    ),
                    ManagedStarter(
                        "{% if use_tauri %}tests{% endif %}/lib/greeting.test.ts",
                        "tests/lib/greeting.test.ts",
                        "// template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_tauri %}src-tauri{% endif %}/build.rs",
                        "src-tauri/build.rs",
                        "// template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_tauri %}src-tauri{% endif %}/src/lib.rs",
                        "src-tauri/src/lib.rs",
                        "// template-code-update",
                    ),
                    ManagedStarter(
                        "{% if use_tauri %}src-tauri{% endif %}/src/main.rs.jinja",
                        "src-tauri/src/main.rs",
                        "// template-code-update",
                    ),
                ),
            ),
        }

        for name, (answers, starters) in cases.items():
            with self.subTest(name=name):
                template = self.copy_template_repository()
                self.commit_repository(template, "template v1")
                project = self.create_versioned_project(template, *answers)

                for starter in starters:
                    template_starter = template / starter.template_path
                    template_starter.write_text(
                        template_starter.read_text() + f"\n{starter.marker}\n"
                    )
                self.commit_repository(template, "template code update")

                updated = self.update_versioned_project(project)

                self.assertEqual(updated.returncode, 0, updated.stdout)
                for starter in starters:
                    self.assertIn(
                        starter.marker,
                        (project / starter.project_path).read_text(),
                    )

    def test_tauri_project_owned_branding_asset_survives_codebase_update(
        self,
    ) -> None:
        for project_state in ProjectFileState:
            with self.subTest(project_state=project_state.value):
                template = self.copy_template_repository()
                self.commit_repository(template, "template v1")
                customized_branding = b"project branding"
                project, project_icon, expected_branding = (
                    self.create_tauri_project_with_branding_asset_state(
                        template,
                        project_state,
                        customized_branding,
                    )
                )
                self.assert_project_owned_branding_asset_survives_codebase_update(
                    template,
                    project,
                    project_icon,
                    expected_branding,
                )


    def test_copier_update_surfaces_conflicting_code_changes(self) -> None:
        template_path = "{% if use_rust %}src{% endif %}/main.rs"
        template, template_starter = self.create_versioned_template(
            template_path,
            "// shared-value-v1\n",
        )
        project = self.create_versioned_project(
            template,
            "use_python=false",
            "use_rust=true",
        )
        project_starter = project / "src/main.rs"
        project_starter.write_text("// project-value\n")
        self.commit_repository(project, "project customization")

        template_starter.write_text("// template-value-v2\n")
        self.commit_repository(template, "template v2")

        updated = self.update_versioned_project(project)

        self.assertEqual(updated.returncode, 0, updated.stdout)
        merged = project_starter.read_text()
        self.assertIn("<<<<<<< before updating", merged)
        self.assertIn("project-value", merged)
        self.assertIn("template-value-v2", merged)


    def test_initial_copy_migrates_existing_code_to_template_standard(self) -> None:
        cases = {
            "python": (
                ("use_python=true", "python_project_kind=package"),
                "src/test_project/__init__.py",
                "",
            ),
            "rust": (
                ("use_python=false", "use_rust=true"),
                "src/main.rs",
                'println!("Hello, world!");',
            ),
            "chrome": (
                ("use_python=false", "use_chrome_extension=true"),
                "src/background.ts",
                "chrome.runtime.onInstalled.addListener",
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                "src/main.ts",
                "registerGreetingForm",
            ),
        }

        for name, (answers, existing_path, template_standard) in cases.items():
            with self.subTest(name=name):
                destination_root = tempfile.TemporaryDirectory()
                self.addCleanup(destination_root.cleanup)
                destination = Path(destination_root.name).resolve() / "project"
                existing = destination / existing_path
                existing.parent.mkdir(parents=True)
                existing.write_text("project-owned code\n")

                preview = self.copy_template_into(
                    destination,
                    *answers,
                    overwrite=True,
                    pretend=True,
                )
                self.assertEqual(preview.returncode, 0, preview.stdout)
                self.assertEqual(existing.read_text(), "project-owned code\n")

                copied = self.copy_template_into(
                    destination,
                    *answers,
                    overwrite=True,
                )
                self.assertEqual(copied.returncode, 0, copied.stdout)
                migrated = existing.read_text()
                self.assertNotEqual(migrated, "project-owned code\n")
                self.assertIn(template_standard, migrated)
                self.assertTrue((destination / ".copier-answers.yml").is_file())

    def test_recopy_restores_deleted_template_code(self) -> None:
        cases = {
            "python_application": (("use_python=true",), "tests/run_pytest.py"),
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
                self.assertTrue(starter.exists())

    def test_newly_enabled_runtime_generates_its_starter_files(self) -> None:
        cases = {
            "python": (
                ("use_python=false",),
                "use_python: false",
                "use_python: true",
                "src/.gitkeep",
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

    def test_reenabled_runtime_restores_template_code(self) -> None:
        cases = {
            "python": (
                ("use_python=true",),
                "use_python: true",
                "use_python: false",
                "tests/run_pytest.py",
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
                self.assertTrue(starter.exists())

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


    def test_project_guidance_template_remains_empty(self) -> None:
        self.assertEqual(
            (REPO_ROOT / ".codex/project.md.jinja").read_bytes(),
            b"",
            "Keep the project guidance template empty for every Copier answer; "
            "put common rules in AGENTS.md and language rules in language guidance.",
        )

    def test_agent_workflow_guidance_and_docs_are_generated(self) -> None:
        configurations = {
            "default": ((), ()),
            "no_runtime": (("use_python=false",), ()),
            "python": (("use_python=true",), ("uv run task check",)),
            "rust": (
                ("use_python=false", "use_rust=true"),
                (
                    "cargo fmt --all --check",
                    "cargo clippy --all-targets --all-features -- -D warnings",
                    "cargo test --all-targets --all-features",
                ),
            ),
            "tauri": (
                ("use_python=false", "use_tauri=true"),
                ("npm run check",),
            ),
            "chrome": (
                (
                    "use_python=false",
                    "use_chrome_extension=true",
                ),
                ("npm run check",),
            ),
            "python_rust_chrome": (
                (
                    "use_python=true",
                    "use_rust=true",
                    "use_chrome_extension=true",
                ),
                (
                    "uv run task check",
                    "cargo fmt --all --check",
                    "cargo clippy --all-targets --all-features -- -D warnings",
                    "cargo test --all-targets --all-features",
                    "npm run check",
                ),
            ),
        }
        required_rules = (
            "Do not make implementation changes directly on `main`.",
            "Use a non-`main` branch for implementation changes.",
            "When subagent tools are available",
            "all required repository quality gates",
            "Generated configuration and source files remain Copier-managed.",
            "copier update --trust --defaults --vcs-ref HEAD",
            "use `copier update`, not `copier recopy`",
        )
        removed_guidance = (
            "Before starting work, create a GitHub Issue",
            "Track implementation changes in GitHub Issues",
            "Creating a GitHub Issue is optional",
            "One class per file",
            "AAA Pattern",
            "git mv <old-path> <new-path>",
            "Determine the languages relevant to the task",
            ".codex/languages/<language>.md",
            "No language guidance is generated",
        )
        linked_docs = {
            "docs/agents/issue-tracker.md": (
                "GitHub Issues",
                "レビュー後の追加課題",
                "`git remote -v`",
                "fork の親だけでは作成先を決めない",
                "GitHub の host・owner・repo",
                "完了条件は役立つ場合だけ加える",
                "`gh issue create --repo HOST/OWNER/REPO`",
                "`gh issue view <number> --repo HOST/OWNER/REPO --comments`",
            ),
            "docs/agents/triage-labels.md": (
                "needs-triage",
                "needs-info",
                "ready-for-agent",
                "ready-for-human",
                "wontfix",
            ),
            "docs/agents/domain.md": (
                "`CONTEXT.md`",
                "`docs/adr/`",
                "存在しない文書は飛ばす",
                "調査だけなら更新は不要",
                "複数のドメインを扱うようになったら",
            ),
        }

        expected_languages = {
            "default": set(),
            "no_runtime": set(),
            "python": {"python"},
            "rust": {"rust"},
            "tauri": {"typescript", "rust"},
            "chrome": {"typescript"},
            "python_rust_chrome": {"python", "rust", "typescript"},
        }
        common_guidance = None
        for name, (answers, quality_commands) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                agents_guidance = (destination / "AGENTS.md").read_text()
                project_guidance = (destination / ".codex/project.md").read_text()
                language_paths = sorted((destination / ".codex/languages").glob("*.md"))
                self.assertEqual(
                    {path.stem for path in language_paths}, expected_languages[name]
                )
                language_guidance = "\n".join(path.read_text() for path in language_paths)
                all_guidance = "\n".join(
                    (agents_guidance, project_guidance, language_guidance)
                )
                self.assertEqual(project_guidance, "")
                additional_instructions, common_section = agents_guidance.split(
                    "## Work boundaries", 1
                )
                if common_guidance is None:
                    common_guidance = common_section
                self.assertEqual(common_section, common_guidance)
                self.assertIn(
                    "Before starting work, read these files", additional_instructions
                )
                referenced_paths = {
                    line.removeprefix("- `").removesuffix("`")
                    for line in additional_instructions.splitlines()
                    if line.startswith("- `")
                }
                self.assertEqual(
                    referenced_paths,
                    {".codex/project.md"}
                    | {
                        f".codex/languages/{language}.md"
                        for language in expected_languages[name]
                    },
                )
                for relative_path in referenced_paths:
                    self.assertTrue((destination / relative_path).is_file())
                self.assertIn("project > language > root common", agents_guidance)
                self.assertIn("repository root", agents_guidance)
                self.assertNotIn("~/.codex", all_guidance)
                self.assertFalse((destination / "AGENTS.repo.md").exists())
                self.assertFalse((destination / "CLAUDE.md").exists())
                self.assertLess(len(common_section.splitlines()), 60)
                self.assertLess(len(agents_guidance.splitlines()), 70)
                for section in (
                    "Delegation",
                    "Verification",
                    "Additional instructions",
                    "Template updates",
                ):
                    self.assertIn(f"## {section}", agents_guidance)
                for section in (
                    "Execution",
                    "Instructions",
                    "Communication",
                    "Language guidance",
                ):
                    self.assertNotIn(f"## {section}", agents_guidance)
                for rule in required_rules:
                    self.assertIn(rule, agents_guidance)
                    self.assertEqual(all_guidance.count(rule), 1, rule)
                for guidance in removed_guidance:
                    self.assertNotIn(guidance, all_guidance)
                for command in quality_commands:
                    self.assertIn(command, language_guidance)
                    self.assertNotIn(command, agents_guidance)
                for language in expected_languages[name]:
                    self.assertEqual(
                        agents_guidance.count(f"`.codex/languages/{language}.md`"), 1
                    )
                for language in {"python", "rust", "typescript"} - expected_languages[name]:
                    self.assertNotIn(f".codex/languages/{language}.md", agents_guidance)
                if name == "tauri":
                    self.assertIn("src-tauri/", language_guidance)
                    self.assertIn("npm run check", language_guidance)
                    self.assertIn(
                        ".codex/languages/typescript.md",
                        (destination / ".codex/languages/rust.md").read_text(),
                    )
                if name == "chrome":
                    self.assertIn("Manifest V3", language_guidance)
                    self.assertIn(".js", language_guidance)

                for relative_path, required_content in linked_docs.items():
                    self.assertIn(f"`{relative_path}`", agents_guidance)
                    generated_doc = destination / relative_path
                    self.assertTrue(generated_doc.is_file(), relative_path)
                    content = generated_doc.read_text()
                    self.assertEqual(content, (REPO_ROOT / relative_path).read_text())
                    for expected in required_content:
                        self.assertIn(expected, content)

    def run_preparation_version_reader(self, destination: Path) -> subprocess.CompletedProcess[str]:
        self.commit_repository(destination, "version fixture")
        script = """
import importlib.util, os, sys
from pathlib import Path
root = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location('release', root / '.github/scripts/release.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
try:
    git = m.Git(root)
    version = m.read_version(git, 'HEAD', m.policy_at(git, 'HEAD'))
    Path(os.environ['GITHUB_OUTPUT']).write_text('version=' + version + '\\n')
except m.PreparationError as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
"""
        return subprocess.run(['python3', '-c', script, str(destination)],
            env={**os.environ, 'GITHUB_OUTPUT': str(destination / 'github-output.txt')},
            capture_output=True, text=True)

    def run_release_version_reader(self, destination: Path, workflow_name: str) -> subprocess.CompletedProcess[str]:
        return self.run_preparation_version_reader(destination)

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


    @staticmethod
    def workflow_step_script(
        destination: Path,
        workflow_name: str,
        step_name: str,
    ) -> str:
        workflow = yaml.safe_load((destination / '.github/workflows' / workflow_name).read_text())
        return next(step['run'] for job in workflow['jobs'].values()
                    for step in job['steps'] if step.get('name') == step_name)

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

    def test_node_runtime_support_uses_typescript_7_oxlint_toolchain(self) -> None:
        configurations = {
            "chrome": ("use_python=false", "use_chrome_extension=true"),
            "tauri": ("use_python=false", "use_tauri=true"),
        }

        for name, answers in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)

                package = json.loads((destination / "package.json").read_text())
                dev_dependencies = package["devDependencies"]

                self.assertEqual(dev_dependencies["typescript"], "^7.0.2")
                self.assertEqual(dev_dependencies["oxlint"], "^1.78.0")
                self.assertEqual(
                    dev_dependencies["oxlint-tsgolint"],
                    "^7.0.2001",
                )
                for obsolete_dependency in (
                    "@eslint/js",
                    "eslint",
                    "globals",
                    "typescript-eslint",
                ):
                    self.assertNotIn(obsolete_dependency, dev_dependencies)
                self.assertEqual(
                    package["scripts"]["lint"],
                    "oxlint --type-aware --deny-warnings .",
                )

                oxlint_config = json.loads(
                    (destination / ".oxlintrc.json").read_text()
                )
                self.assertTrue(oxlint_config["options"]["typeAware"])
                self.assertTrue(
                    any(
                        "typescript" in override.get("plugins", [])
                        for override in oxlint_config["overrides"]
                    )
                )
                self.assertFalse((destination / "eslint.config.mjs").exists())

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
            "tauri_release": (
                ("use_tauri=true", "use_gh_actions_tauri_build=true"),
                "tauri-build.yml",
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
                    f"GIT_AUTHOR_NAME: {json.dumps(author_name)}",
                    workflow,
                )
                self.assertIn(
                    f"GIT_AUTHOR_EMAIL: {json.dumps(author_email)}",
                    workflow,
                )
                self.assertNotIn("Read git author", workflow)
                self.assertNotIn("git log -1", workflow)
                self.assertNotIn("steps.author.outputs", workflow)

                copier_answers = (destination / ".copier-answers.yml").read_text()
                self.assertIn(author_email, copier_answers)

    def test_release_workflows_use_tag_as_release_title(self) -> None:
        configurations = {
            "release": (
                ("use_python=false", "use_gh_actions_release=true"),
                "release.yml",
            ),
            "docker": (
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
            "chrome": (
                (
                    "use_python=false",
                    "use_chrome_extension=true",
                    "use_gh_actions_chrome_extension_release=true",
                ),
                "chrome-extension-release.yml",
            ),
            "tauri": (
                ("use_tauri=true", "use_gh_actions_tauri_build=true"),
                "tauri-build.yml",
            ),
        }

        for name, (answers, workflow_name) in configurations.items():
            with self.subTest(name=name):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                workflow = (
                    destination / ".github/workflows" / workflow_name
                ).read_text()
                self.assertIn('--title "$TAG"', workflow)
                self.assertEqual(workflow.count("--title "), 1)
                if name == "tauri":
                    continue  # The stateful Tauri publication tests exercise this path.
                script = self.workflow_step_script(
                    destination, workflow_name, "Create draft" if name == "chrome" else "Create GitHub Release"
                )
                for expression, value in {
                    "${{ github.server_url }}": "https://github.com",
                    "${{ github.repository }}": "mizucopo/example",
                    "${{ steps.release-metadata.outputs.release_notes_path }}":
                        str(destination / "release-notes.md"),
                }.items():
                    script = script.replace(expression, value)
                capture_path = destination / "release-arguments"
                for tag in ("1.2.3", "v1.2.3", "1.2.3-rc.1"):
                    with self.subTest(name=name, tag=tag):
                        executed = self.run_process(
                            ["bash"], destination,
                            env={
                                **os.environ,
                                "TAG": tag,
                                "ZIP_PATH": str(destination / "distribution.zip"),
                                "ZIP_PREFIX": "Custom App",
                                "RUNNER_TEMP": str(destination),
                                "CAPTURED_ARGS": str(capture_path),
                            },
                            script=(
                                "gh() { printf '%s\\0' \"$@\" > \"$CAPTURED_ARGS\"; }\n"
                                + script
                            ),
                        )
                        self.assertEqual(executed.returncode, 0, executed.stdout)
                        args = capture_path.read_text().split("\0")[:-1]
                        self.assertEqual(args[:3], ["release", "create", tag])
                        self.assertEqual(args[args.index("--title") + 1], tag)

    def test_generated_release_workflows_pass_git_diff_check(self) -> None:
        configurations = {
            "release": (
                "use_python=false",
                "use_gh_actions_release=true",
            ),
            "docker_release": (
                "use_python=false",
                "use_docker=true",
                "use_gh_actions_docker_release=true",
            ),
            "chrome_extension_release": (
                "use_python=false",
                "use_chrome_extension=true",
                "use_gh_actions_chrome_extension_release=true",
            ),
            "tauri_release": (
                "use_tauri=true",
                "use_gh_actions_tauri_build=true",
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

    def test_docker_release_platform_selection_controls_build_and_emulation(self) -> None:
        selections = {
            "unspecified": ((), None, False),
            "empty": (("docker_release_platforms=[]",), None, False),
            "amd64": (
                ("docker_release_platforms=[linux/amd64]",),
                'platforms: "linux/amd64"',
                False,
            ),
            "arm64": (
                ("docker_release_platforms=[linux/arm64]",),
                'platforms: "linux/arm64"',
                True,
            ),
            "both": (
                ("docker_release_platforms=[linux/amd64,linux/arm64]",),
                'platforms: "linux/amd64,linux/arm64"',
                True,
            ),
        }
        for use_ecr in (False, True):
            for name, (selection, expected_platforms, needs_qemu) in selections.items():
                with self.subTest(ecr=use_ecr, selection=name):
                    result, destination = self.copy_template(
                        "use_docker=true",
                        "use_gh_actions_docker_release=true",
                        f"use_aws_ecr={str(use_ecr).lower()}",
                        *selection,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout)
                    workflow = (
                        destination / ".github/workflows/docker-release.yml"
                    ).read_text()
                    build_step = workflow.split("      - name: Build and push immutable image\n", 1)[1]
                    build_step = build_step.split("      - name:", 1)[0]
                    self.assertIn(
                        "if: steps.image-state.outputs.version_exists != 'true'",
                        build_step,
                    )
                    if expected_platforms is None:
                        self.assertNotIn("platforms:", workflow)
                    else:
                        self.assertIn(expected_platforms, build_step)
                    self.assertEqual("Set up QEMU" in workflow, needs_qemu)
                    if needs_qemu:
                        self.assertIn(
                            "      - name: Set up QEMU\n"
                            "        if: steps.image-state.outputs.version_exists != 'true'",
                            workflow,
                        )
                        self.assertLess(
                            workflow.index("      - name: Set up QEMU"),
                            workflow.index("      - name: Set up Docker Buildx"),
                        )
                        qemu_step = workflow.split("      - name: Set up QEMU\n", 1)[1]
                        qemu_step = qemu_step.split("      - name:", 1)[0]
                        self.assertIn("platforms: arm64", qemu_step)
                        self.assertRegex(
                            qemu_step, r"docker/setup-qemu-action@[0-9a-f]{40}"
                        )

                    # Promotion must copy the complete published manifest, without
                    # rebuilding it or filtering it to the current runner's platform.
                    latest_script = self.workflow_step_script(
                        destination, "docker-release.yml", "Publish latest image"
                    )
                    self.assertEqual(latest_script.strip(),
                        'docker buildx imagetools create --prefer-index=false --tag "$IMAGE_REPOSITORY:latest" "$IMAGE_REPOSITORY:$TAG"')
                    promotion = workflow.split("  promote-latest:\n", 1)[1]
                    self.assertNotIn("Set up QEMU", promotion)
                    self.assertNotIn("platforms:", promotion)

    def test_docker_release_platform_selection_rejects_unsupported_platforms(self) -> None:
        for platforms in (
            "[linux/arm/v7]",
            "[windows/amd64]",
            "[linux/amd64,linux/riscv64]",
        ):
            with self.subTest(platforms=platforms):
                result, _ = self.copy_template(
                    "use_docker=true",
                    "use_gh_actions_docker_release=true",
                    f"docker_release_platforms={platforms}",
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("docker_release_platforms", result.stdout)

    def test_docker_release_platform_answer_is_only_saved_for_docker_release(self) -> None:
        for answers in (
            (),
            ("use_docker=true",),
            ("use_docker=true", "use_version_management=false"),
            ("use_docker=true", "use_gh_actions_docker_quality=true"),
        ):
            with self.subTest(answers=answers):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertNotIn(
                    "docker_release_platforms:",
                    (destination / ".copier-answers.yml").read_text(),
                )
                self.assertFalse(
                    (destination / ".github/workflows/docker-release.yml").exists()
                )

    def test_docker_release_platform_selection_survives_updates_and_can_be_cleared(
        self,
    ) -> None:
        template = self.copy_template_repository()
        self.commit_repository(template, "template with Docker release platforms")
        project = self.create_versioned_project(
            template, "use_docker=true", "use_gh_actions_docker_release=true"
        )
        answers_path = project / ".copier-answers.yml"
        workflow_path = project / ".github/workflows/docker-release.yml"
        native_workflow = workflow_path.read_text()
        self.assertIn("docker_release_platforms: []\n", answers_path.read_text())

        # Existing projects have no saved answer for the newly added question.
        answers_path.write_text(
            answers_path.read_text().replace("docker_release_platforms: []\n", "")
        )
        self.commit_repository(project, "answers without platform selection")
        updated = self.update_versioned_project(project)
        self.assertEqual(updated.returncode, 0, updated.stdout)
        self.assertEqual(workflow_path.read_text(), native_workflow)
        self.assertIn("docker_release_platforms: []\n", answers_path.read_text())
        self.commit_repository(project, "default to native platform")

        updated = self.update_versioned_project(
            project, "docker_release_platforms=[linux/amd64,linux/arm64]"
        )
        self.assertEqual(updated.returncode, 0, updated.stdout)
        self.assertIn(
            "docker_release_platforms:\n- linux/amd64\n- linux/arm64\n",
            answers_path.read_text(),
        )
        multi_platform_workflow = workflow_path.read_text()
        self.assertIn('platforms: "linux/amd64,linux/arm64"', multi_platform_workflow)
        self.assertIn("Set up QEMU", multi_platform_workflow)
        self.commit_repository(project, "select both platforms")

        updated = self.update_versioned_project(project)
        self.assertEqual(updated.returncode, 0, updated.stdout)
        self.assertEqual(workflow_path.read_text(), multi_platform_workflow)

        updated = self.update_versioned_project(project, "docker_release_platforms=[]")
        self.assertEqual(updated.returncode, 0, updated.stdout)
        self.assertEqual(workflow_path.read_text(), native_workflow)
        self.assertIn("docker_release_platforms: []\n", answers_path.read_text())

    def test_docker_hub_login_username_is_separate_from_image_namespace(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_python=false",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
            "docker_registry=image-owner",
            "docker_login_username=release-bot",
            "docker_image_name=test-project",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        docker_release = (
            destination / ".github/workflows/docker-release.yml"
        ).read_text()
        pr_tag_check = (
            destination / ".github/release.json"
        ).read_text()

        self.assertIn('DOCKERHUB_USERNAME: "release-bot"', docker_release)
        self.assertNotIn("DOCKERHUB_USERNAME", pr_tag_check)
        self.assertIn('"repository": "image-owner/test-project"', pr_tag_check)
        self.assertIn('username: "release-bot"', docker_release)
        self.assertIn(
            'IMAGE_REPOSITORY: "image-owner/test-project"',
            docker_release,
        )
        self.assertIn('--tag "$IMAGE_REPOSITORY:latest"', docker_release)
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
        self.assertIn(
            f'IMAGE_REPOSITORY: "{registry}/test-project"',
            docker_release,
        )
        self.assertIn('--tag "$IMAGE_REPOSITORY:latest"', docker_release)
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
                    "Inspect GitHub Release" if name == 'docker_release' else "Inspect release state",
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
                    state_env["RELEASE_ASSET_NAME"] = "project-0.1.0.zip"

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

    def test_existing_chrome_project_standardization_migration(self) -> None:
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
            """chrome.runtime.onInstalled.addListener(({ reason }) => {
  if (reason === "install") {
    console.info("Standard Extension installed");
  }
});
""",
        )
        self.assertEqual(
            (destination / "tests/lib/extension-title.test.ts").read_text(),
            (
                REPO_ROOT
                / "{% if use_chrome_extension %}tests{% endif %}"
                / "lib/extension-title.test.ts"
            ).read_text(),
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
            ".oxlintrc.json",
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
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        policy = json.loads((destination / '.github/release.json').read_text())
        self.assertEqual([source['path'] for source in policy['version']['sources']], ['package.json', 'src/manifest.json'])

    def test_chrome_preparation_validates_scaffold_manifest_version_source(
        self,
    ) -> None:
        result, destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
            "chrome_extension_version=1.2.3",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        valid_result = self.run_preparation_version_reader(destination)
        self.assertEqual(valid_result.returncode, 0, valid_result.stdout)
        output = (destination / "github-output.txt").read_text()
        self.assertIn("version=1.2.3", output)

        manifest_path = destination / "src/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = "1.2.4"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

        mismatch_result = self.run_preparation_version_reader(destination)
        self.assertNotEqual(mismatch_result.returncode, 0)
        self.assertIn("version sources disagree", mismatch_result.stderr)

    def test_chrome_preparation_rejects_invalid_manifest_version(self) -> None:
        result, destination = self.copy_template(
            "use_chrome_extension=true",
            "use_gh_actions_chrome_extension_release=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        manifest_path = destination / "src/manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = "1.2.3-beta.1"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

        invalid_result = self.run_preparation_version_reader(destination)
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertIn("version sources disagree", invalid_result.stderr)

    def test_chrome_distribution_release_workflow_is_opt_in(self) -> None:
        result, destination = self.copy_template("use_chrome_extension=true")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(
            (destination / ".github/workflows/chrome-extension-release.yml").exists()
        )


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
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        policy = json.loads((destination / '.github/release.json').read_text())
        self.assertEqual([source['path'] for source in policy['version']['sources']], ['pyproject.toml'])

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
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        policy = json.loads((destination / '.github/release.json').read_text())
        self.assertEqual([source['path'] for source in policy['version']['sources']], ['Cargo.toml'])

    def test_rust_version_source_is_used_for_docker_release(self) -> None:
        result, destination = self.copy_template(
            "use_rust=true",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        policy = json.loads((destination / '.github/release.json').read_text())
        self.assertEqual([source['path'] for source in policy['version']['sources']], ['Cargo.toml'])

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
        self.assertIn("run: npm run check", workflow)
        self.assertIn("libwebkit2gtk-4.1-dev", workflow)
        self.assertIn("libxdo-dev", workflow)
        self.assertTrue((destination / "src-tauri/icons/icon.png").exists())
        self.assertFalse((destination / "Cargo.toml").exists())
        self.assertFalse((destination / "src/main.rs").exists())
        self.assertFalse((destination / "version").exists())

    def test_tauri_build_workflow_is_opt_in_and_requires_tauri(self) -> None:
        configurations = (
            ((), False),
            (("use_tauri=true",), False),
            (("use_tauri=true", "use_gh_actions_tauri_build=false"), False),
            (("use_gh_actions_tauri_build=true",), False),
            (("use_tauri=true", "use_gh_actions_tauri_build=true"), True),
        )
        for answers, expected in configurations:
            with self.subTest(answers=answers):
                result, destination = self.copy_template(*answers)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(
                    (destination / ".github/workflows/tauri-build.yml").exists(),
                    expected,
                )
                saved_answers = (destination / ".copier-answers.yml").read_text()
                if "use_tauri=true" in answers:
                    self.assertIn(
                        f"use_gh_actions_tauri_build: {str(expected).lower()}\n",
                        saved_answers,
                    )
                elif not answers:
                    self.assertNotIn("use_gh_actions_tauri_build:", saved_answers)

    def test_tauri_build_releases_three_zips_after_main_merge(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "use_gh_actions_tauri_build=true",
            "tauri_product_name=Custom App",
            "tauri_version=1.2.3",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        workflow = (destination / ".github/workflows/tauri-build.yml").read_text()
        self.assertIn("on:\n  push:\n    branches:\n      - main", workflow)
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn(
            "permissions:\n  contents: read\n  pull-requests: read", workflow
        )
        parsed = yaml.safe_load(workflow)
        for name in ('prepare', 'preflight', 'publish', 'promote-latest'):
            self.assertEqual(parsed['jobs'][name]['permissions']['contents'], 'write')
        self.assertEqual(workflow.count("contents: write"), 4)
        self.assertIn("Verify numbering commit", workflow)
        self.assertIn("Run quality gate\n        run: npm run check", workflow)
        self.assertIn("fail-fast: false", workflow)
        self.assertIn(
            "needs.quality.result == 'success' && needs.build.result == 'success'",
            workflow,
        )
        self.assertIn(
            "steps.release-state.outputs.release_exists == 'true' "
            "&& steps.release-state.outputs.release_is_draft != 'true' "
            "&& steps.release-state.outputs.release_asset_exists != 'true'",
            workflow,
        )
        for platform, runner, target in (
            ("windows-x64", "windows-latest", "x86_64-pc-windows-msvc"),
            ("windows-arm64", "windows-11-arm", "aarch64-pc-windows-msvc"),
            ("macos-arm64", "macos-latest", "aarch64-apple-darwin"),
        ):
            self.assertIn(
                f"- platform: {platform}\n"
                f"            runner: {runner}\n"
                f"            target: {target}", workflow,
            )
            self.assertIn(f"$ZIP_PREFIX-{platform}.zip", workflow)
        for expected in (
            'node-version-file: ".node-version"',
            'rustup target add "$RUST_TARGET"',
            'npm run tauri -- build --target "$RUST_TARGET" --no-bundle',
            'npm run tauri -- build --target "$RUST_TARGET" --bundles app',
            'APPLE_SIGNING_IDENTITY: "-"',
            "Compress-Archive -LiteralPath",
            "ditto -c -k --keepParent",
            "archive: false",
            "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1",
            "pattern: ${{ needs.preflight.outputs.zip_prefix }}-*.zip",
            "merge-multiple: true",
            "skip-decompress: true",
            "if-no-files-found: error",
            'gh release create "$TAG"',
            'run: gh release edit "$TAG" --latest',
        ):
            self.assertIn(expected, workflow)
        self.assertNotIn("Custom App", workflow)
        self.assertNotIn("1.2.3", workflow)
        self.assertNotIn("name: tauri-${{ matrix.platform }}", workflow)
        self.assertFalse((destination / ".github/workflows/release.yml").exists())
        self.assertTrue(
            (destination / ".github/scripts/release.py").exists()
        )

    def test_tauri_release_marks_semver_prereleases(self) -> None:
        for version, prerelease in (
            ("1.0.0", False),
            ("1.0.0-rc.1", True),
            ("1.0.0+build-x", False),
            ("1.0.0-rc.1+build-x", True),
        ):
            with self.subTest(version=version):
                result, project = self.copy_template(
                    "use_python=false",
                    "use_tauri=true",
                    "use_gh_actions_tauri_build=true",
                    f"tauri_version={version}",
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                read_version = self.run_release_version_reader(project, "tauri-build.yml")
                self.assertEqual(read_version.returncode, 0, read_version.stdout)
                self.assertEqual(
                    (project / "github-output.txt").read_text(), f"version={version}\n"
                )

                output = project / "metadata-output.txt"
                event_path = project / "push-event.json"
                event_path.write_text(json.dumps({
                    "repository": {"full_name": "owner/project"}
                }))
                metadata = self.run_process(
                    ["bash", "-e"], project,
                    script=self.workflow_step_script(
                        project, "tauri-build.yml", "Prepare release asset names"
                    ),
                    env={
                        **os.environ,
                        "GITHUB_REPOSITORY": "owner/project",
                        "GITHUB_EVENT_PATH": str(event_path),
                        "GITHUB_OUTPUT": str(output),
                        "VERSION": version,
                    },
                )
                self.assertEqual(metadata.returncode, 0, metadata.stdout)
                values = dict(line.split("=", 1) for line in output.read_text().splitlines())
                zip_prefix = f"project-{version}"
                self.assertEqual(values["zip_prefix"], zip_prefix)
                self.assertEqual(values["is_prerelease"], str(prerelease).lower())

    def test_tauri_latest_promotion_requires_stable_release(self) -> None:
        result, project = self.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        workflow = (project / ".github/workflows/tauri-build.yml").read_text()
        self.assertIn(
            "      is_prerelease: ${{ steps.metadata.outputs.is_prerelease }}\n",
            workflow,
        )
        create = workflow.split("      - name: Create GitHub Release\n", 1)[1]
        create = create.split("      - name:", 1)[0]
        self.assertIn(
            "          IS_PRERELEASE: ${{ needs.preflight.outputs.is_prerelease }}\n",
            create,
        )
        promotion = workflow.split("  promote-latest:\n", 1)[1]
        self.assertIn("    needs: [preflight, publish]\n", promotion)
        self.assertIn("    if: needs.preflight.outputs.is_prerelease == 'false'\n", promotion)
        self.assertIn('run: gh release edit "$TAG" --latest', promotion)

    def test_tauri_build_conflicts_with_other_release_workflows(self) -> None:
        for conflicting in (
            "use_gh_actions_release=true",
            "use_gh_actions_docker_release=true",
        ):
            with self.subTest(conflicting=conflicting):
                result, _ = self.copy_template(
                    "use_tauri=true", "use_gh_actions_tauri_build=true",
                    conflicting,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Tauri配布release workflow", result.stdout)

    def test_tauri_release_version_requires_matching_manifests(self) -> None:
        result, project = self.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        for workflow_name in ("tauri-build.yml",):
            valid = self.run_release_version_reader(project, workflow_name)
            self.assertEqual(valid.returncode, 0, valid.stdout)

        package_path = project / "package.json"
        package = json.loads(package_path.read_text())
        package["version"] = "1.2.3"
        package_path.write_text(json.dumps(package) + "\n")
        for workflow_name in ("tauri-build.yml",):
            mismatch = self.run_release_version_reader(project, workflow_name)
            self.assertNotEqual(mismatch.returncode, 0, mismatch.stdout)
            self.assertIn("version sources disagree", mismatch.stderr)

        config_path = project / "src-tauri/tauri.conf.json"
        config = json.loads(config_path.read_text())
        config["version"] = "1.2.3"
        config_path.write_text(json.dumps(config) + "\n")
        cargo_path = project / "src-tauri/Cargo.toml"
        cargo_path.write_text(
            cargo_path.read_text().replace('version = "0.1.0"', 'version = "1.2.3"')
        )
        for workflow_name in ("tauri-build.yml",):
            valid = self.run_release_version_reader(project, workflow_name)
            self.assertEqual(valid.returncode, 0, valid.stdout)
            self.assertEqual((project / "github-output.txt").read_text(), "version=1.2.3\n")

        package["version"] = "1.2"
        package_path.write_text(json.dumps(package) + "\n")
        config['version'] = '1.2'
        config_path.write_text(json.dumps(config) + '\n')
        cargo_path.write_text(cargo_path.read_text().replace('version = "1.2.3"', 'version = "1.2"'))
        invalid = self.run_release_version_reader(project, "tauri-build.yml")
        self.assertNotEqual(invalid.returncode, 0, invalid.stdout)
        self.assertIn("Invalid SemVer", invalid.stderr)

    def test_tauri_release_reuses_original_assets_after_repository_rename(self) -> None:
        result, project = self.copy_template(
            "use_python=false", "use_tauri=true", "use_gh_actions_tauri_build=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.commit_repository(project, "Release source")
        later_commit = self.run_process(
            ["git", "commit", "--allow-empty", "-m", "Release commit"], project
        )
        self.assertEqual(later_commit.returncode, 0, later_commit.stdout)
        origin = project.parent / "origin.git"
        init_origin = self.run_process(
            ["git", "init", "--bare", str(origin)], project
        )
        self.assertEqual(init_origin.returncode, 0, init_origin.stdout)
        add_origin = self.run_process(
            ["git", "remote", "add", "origin", str(origin)], project
        )
        self.assertEqual(add_origin.returncode, 0, add_origin.stdout)

        mock_bin = project.parent / "mock-bin"
        mock_bin.mkdir()
        curl = mock_bin / "curl"
        curl.write_text(
            '#!/bin/sh\noutput=\nwhile [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = "--output" ]; then shift; output=$1; fi\n'
            '  url=$1\n'
            '  shift\ndone\nprintf "%s" "$FAKE_RELEASE_JSON" > "$output"\n'
            'printf "%s" "$url" > "$FAKE_REQUEST_PATH"\n'
            'printf "%s" "$FAKE_HTTP_STATUS"\n'
            'exit "${FAKE_CURL_EXIT:-0}"\n'
        )
        curl.chmod(0o755)
        state_script = self.workflow_step_script(
            project, "tauri-build.yml", "Inspect release state"
        )
        gh = mock_bin / "gh"
        gh.write_text(
            '#!/bin/sh\n'
            'if [ "$FAKE_HTTP_STATUS" = "404" ]; then\n'
            '  printf "[[]]"\n'
            'else\n'
            '  printf \'[[%s]]\' "$FAKE_RELEASE_JSON"\n'
            'fi\n'
        )
        gh.chmod(0o755)
        metadata_script = self.workflow_step_script(
            project, "tauri-build.yml", "Prepare release asset names"
        )
        guard_script = self.workflow_step_script(
            project, "tauri-build.yml", "Reject incomplete immutable release"
        )
        decision_script = self.workflow_step_script(
            project, "tauri-build.yml", "Decide whether to build"
        )
        platforms = ("windows-x64", "windows-arm64", "macos-arm64")
        original_assets = [f"original-app-0.1.0-{p}.zip" for p in platforms]
        renamed_assets = [f"renamed-app-0.1.0-{p}.zip" for p in platforms]
        other_assets = [f"other-app-0.1.0-{p}.zip" for p in platforms]
        event_path = project / "push-event.json"
        request_path = project / "request-url.txt"
        output = project / "github-output.txt"
        for name, current_name, event_name, assets, http_status, tag_ref, expected in (
            ("first", "original-app", "original-app", [], "404", None, "build"),
            ("tag-only", "original-app", "original-app", [], "404", "HEAD", "build"),
            ("same-name-rerun", "original-app", "original-app", original_assets,
             "200", "HEAD", "skip"),
            ("renamed-rerun", "renamed-app", "original-app", original_assets,
             "200", "HEAD", "skip"),
            ("new-push", "renamed-app", "renamed-app", [], "404", None, "build"),
            ("new-name-rerun", "renamed-app", "renamed-app", renamed_assets,
             "200", "HEAD", "skip"),
            ("missing-x64", "renamed-app", "original-app", original_assets[1:],
             "200", "HEAD", "incomplete"),
            ("missing-arm64", "renamed-app", "original-app", original_assets[::2],
             "200", "HEAD", "incomplete"),
            ("missing-macos", "renamed-app", "original-app", original_assets[:2],
             "200", "HEAD", "incomplete"),
            ("mixed-prefixes", "renamed-app", "original-app",
             original_assets[:2] + renamed_assets[2:], "200", "HEAD", "incomplete"),
            ("ambiguous-prefixes", "renamed-app", "original-app",
             renamed_assets + other_assets, "200", "HEAD", "incomplete"),
            ("wrong-commit", "renamed-app", "original-app", original_assets,
             "200", "HEAD^", "error"),
            ("release-without-tag", "renamed-app", "original-app", original_assets,
             "200", None, "error"),
            ("api-failure", "renamed-app", "original-app", original_assets,
             "500", "HEAD", "error"),
            ("auth-failure", "renamed-app", "original-app", original_assets,
             "403", "HEAD", "error"),
            ("transport-failure", "renamed-app", "original-app", original_assets,
             "200", "HEAD", "error"),
            ("invalid-response", "renamed-app", "original-app", original_assets,
             "200", "HEAD", "error"),
        ):
            with self.subTest(name=name):
                self.run_process(["git", "tag", "-d", "0.1.0"], project)
                if tag_ref is not None:
                    tag = self.run_process(["git", "tag", "0.1.0", tag_ref], project)
                    self.assertEqual(tag.returncode, 0, tag.stdout)
                event_path.write_text(json.dumps({
                    "repository": {"full_name": f"owner/{event_name}"}
                }))
                env = {
                    **os.environ,
                    "PATH": f"{mock_bin}{os.pathsep}{os.environ['PATH']}",
                    "FAKE_RELEASE_JSON": json.dumps({
                        "id": 41, "tag_name": "0.1.0", "draft": False,
                        "assets": [
                            {"name": asset, "state": "uploaded", "size": 100}
                            for asset in assets
                        ],
                    }) if name != "invalid-response" else "not-json",
                    "FAKE_HTTP_STATUS": http_status,
                    "FAKE_CURL_EXIT": "7" if name == "transport-failure" else "0",
                    "FAKE_REQUEST_PATH": str(request_path),
                    "GITHUB_API_URL": "https://api.github.example",
                    "GITHUB_REPOSITORY": f"owner/{current_name}",
                    "GITHUB_EVENT_PATH": str(event_path),
                    "GITHUB_OUTPUT": str(output),
                    "GH_TOKEN": "test-token",
                    "VERSION": "0.1.0",
                    "TAG": "0.1.0",
                    "INSPECT_DRAFT_RELEASES": "true",
                }
                output.unlink(missing_ok=True)
                request_path.unlink(missing_ok=True)
                metadata = self.run_process(
                    ["bash", "-e"], project, script=metadata_script, env=env
                )
                self.assertEqual(metadata.returncode, 0, metadata.stdout)
                values = dict(
                    line.split("=", 1) for line in output.read_text().splitlines()
                )
                self.assertEqual(values["zip_prefix"], f"{event_name}-0.1.0")
                self.assertEqual(values["is_prerelease"], "false")
                env["RELEASE_ASSET_NAMES"] = "|".join(
                    f"{values['zip_prefix']}-{platform}.zip" for platform in platforms
                )
                output.unlink()
                state = self.run_process(
                    ["bash"], project, script=state_script, env=env
                )
                if request_path.exists():
                    self.assertEqual(
                        request_path.read_text(),
                        f"https://api.github.example/repos/owner/{current_name}/releases/tags/0.1.0",
                    )
                if expected == "error":
                    self.assertNotEqual(state.returncode, 0, state.stdout)
                    self.assertFalse(output.exists())
                    continue
                self.assertEqual(state.returncode, 0, state.stdout)
                values = dict(
                    line.split("=", 1) for line in output.read_text().splitlines()
                )
                self.assertEqual(values["tag_exists"], str(tag_ref is not None).lower())
                self.assertEqual(
                    values["release_exists"], str(http_status == "200").lower()
                )
                self.assertEqual(
                    values["release_asset_exists"], str(expected == "skip").lower()
                )
                if expected == "incomplete":
                    guard = self.run_process(
                        ["bash", "-e"], project, script=guard_script, env=env
                    )
                    self.assertNotEqual(guard.returncode, 0, guard.stdout)
                    self.assertIn(
                        "missing one or more Tauri distribution ZIP assets", guard.stdout
                    )
                    continue
                output.unlink()
                decision = self.run_process(
                    ["bash", "-e"], project, script=decision_script,
                    env={**env, "RELEASE_ASSET_EXISTS": values["release_asset_exists"]},
                )
                self.assertEqual(decision.returncode, 0, decision.stdout)
                self.assertEqual(
                    output.read_text(), f"needs_build={str(expected == 'build').lower()}\n"
                )

    def test_tauri_release_rejects_unverifiable_push_repository(self) -> None:
        result, project = self.copy_template(
            "use_python=false", "use_tauri=true", "use_gh_actions_tauri_build=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        script = self.workflow_step_script(
            project, "tauri-build.yml", "Prepare release asset names"
        )
        output = project / "github-output.txt"
        event_path = project / "push-event.json"
        invalid_events = ["not-json", "{}", "null", "[]", '{}\n{}']
        for full_name in (
            None, 42, [], {}, "", "owner", "owner/", "/project",
            "owner/project/extra", " owner/project", "owner/project ",
            "owner/project\n", "owner/project\r", "owner/project\ninjected=true",
            "owner/project|extra", "owner/project$(command)",
        ):
            invalid_events.append(json.dumps({
                "repository": {"full_name": full_name}
            }))
        for event in invalid_events:
            with self.subTest(event=event):
                event_path.write_text(event)
                output.unlink(missing_ok=True)
                metadata = self.run_process(
                    ["bash", "-e"], project, script=script,
                    env={
                        **os.environ,
                        "GITHUB_REPOSITORY": "owner/current-app",
                        "GITHUB_EVENT_PATH": str(event_path),
                        "GITHUB_OUTPUT": str(output),
                        "VERSION": "0.1.0",
                    },
                )
                self.assertNotEqual(metadata.returncode, 0, metadata.stdout)
                self.assertIn("::error::", metadata.stdout)
                self.assertFalse(output.exists())

        event_path.unlink()
        for path in (None, "", str(event_path)):
            with self.subTest(path=path):
                env = {
                    **os.environ,
                    "GITHUB_REPOSITORY": "owner/current-app",
                    "GITHUB_OUTPUT": str(output),
                    "VERSION": "0.1.0",
                }
                env.pop("GITHUB_EVENT_PATH", None)
                if path is not None:
                    env["GITHUB_EVENT_PATH"] = path
                metadata = self.run_process(
                    ["bash", "-e"], project, script=script, env=env
                )
                self.assertNotEqual(metadata.returncode, 0, metadata.stdout)
                self.assertIn("::error::", metadata.stdout)
                self.assertFalse(output.exists())

    def test_tauri_build_update_preserves_project_and_can_be_disabled(self) -> None:
        template = self.copy_template_repository()
        config_path = template / "copier.yml"
        current_config = config_path.read_text()
        option_start = current_config.index("use_gh_actions_tauri_build:\n")
        option_end = current_config.index(
            "use_gh_actions_chrome_extension_release:\n", option_start
        )
        config_path.write_text(current_config[:option_start] + current_config[option_end:])
        workflow_template = template / (
            ".github/workflows/"
            "{% if use_tauri and use_gh_actions_tauri_build %}"
            "tauri-build.yml{% endif %}.jinja"
        )
        current_workflow = workflow_template.read_text()
        workflow_template.unlink()
        self.commit_repository(template, "template before optional Tauri build")
        project = self.create_versioned_project(template, "use_tauri=true")
        app_config_path = project / "src-tauri/tauri.conf.json"
        app_config = json.loads(app_config_path.read_text())
        app_config["productName"] = "Project App"
        app_config_path.write_text(json.dumps(app_config, indent=2) + "\n")
        (project / "src/main.ts").write_text("// project-specific implementation\n")
        (project / "src-tauri/icons/icon.png").write_bytes(b"project-specific icon")
        preserved = {
            path: (project / path).read_bytes()
            for path in (
                "src-tauri/tauri.conf.json", "src/main.ts", "src-tauri/icons/icon.png",
                "package.json", "src-tauri/Cargo.toml",
            )
        }
        self.commit_repository(project, "customize application")
        config_path.write_text(current_config)
        workflow_template.write_text(current_workflow)
        self.commit_repository(template, "add optional Tauri build")
        workflow_path = project / ".github/workflows/tauri-build.yml"
        for answers, expected in (
            ((), False),
            (("use_gh_actions_tauri_build=true",), True),
            ((), True),
            (("use_gh_actions_tauri_build=false",), False),
        ):
            with self.subTest(answers=answers, expected=expected):
                updated = self.update_versioned_project(project, *answers)
                self.assertEqual(updated.returncode, 0, updated.stdout)
                self.assertEqual(workflow_path.exists(), expected)
                self.assertIn(
                    f"use_gh_actions_tauri_build: {str(expected).lower()}\n",
                    (project / ".copier-answers.yml").read_text(),
                )
                for path, content in preserved.items():
                    self.assertEqual((project / path).read_bytes(), content, path)
                if self.run_process(["git", "status", "--porcelain"], project).stdout:
                    self.commit_repository(project, "apply Tauri build selection")

    def run_tauri_artifact_preparation(
        self, project: Path, target: str, platform: str,
        *, target_directory: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        output = project / "artifact-output"
        output.write_text("")
        mock_bin = project / "mock-bin"
        mock_bin.mkdir(exist_ok=True)
        cargo = mock_bin / "cargo"
        cargo.write_text('#!/bin/sh\nprintf \'%s\\n\' "$TEST_CARGO_METADATA"\n')
        cargo.chmod(0o755)
        result = self.run_process(
            ["bash", "-e", "-o", "pipefail"],
            project,
            env={
                **os.environ,
                "PATH": f"{mock_bin}{os.pathsep}{os.environ['PATH']}",
                "TEST_CARGO_METADATA": json.dumps({
                    "target_directory": str(target_directory or project / "src-tauri/target")
                }),
                "RUST_TARGET": target,
                "ARTIFACT_PLATFORM": platform,
                "ZIP_PREFIX": "test-project-0.1.0",
                "RUNNER_TEMP": str(project / "runner temp" / platform),
                "GITHUB_OUTPUT": str(output),
            },
            script=self.workflow_step_script(
                project, "tauri-build.yml", "Create macOS distribution ZIP"
            ),
        )
        return result, output

    def uploaded_tauri_artifact(self, output: Path) -> Path:
        pattern = output.read_text().strip().removeprefix("path=")
        files = []
        for match in glob.glob(pattern, include_hidden=True):
            path = Path(match)
            files.extend(path.rglob("*") if path.is_dir() else [path])
        files = [path for path in files if path.is_file()]
        self.assertEqual(len(files), 1, files)
        return files[0]

    def test_tauri_macos_artifact_supports_glob_characters_in_app_names(self) -> None:
        result, project = self.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true",
            "tauri_product_name=Desk[1]",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        target = "aarch64-apple-darwin"
        app = project / f"src-tauri/target/{target}/release/bundle/macos/Desk[1].app"
        app.mkdir(parents=True)
        (app / "application").write_bytes(b"application")
        prepared, output = self.run_tauri_artifact_preparation(
            project, target, "macos-arm64"
        )
        self.assertEqual(prepared.returncode, 0, prepared.stdout)
        artifact = self.uploaded_tauri_artifact(output)
        self.assertEqual(artifact.name, "test-project-0.1.0-macos-arm64.zip")
        archive_entries = self.run_process(
            ["unzip", "-Z", "-1", str(artifact)], project
        )
        self.assertEqual(archive_entries.returncode, 0, archive_entries.stdout)
        self.assertIn("Desk[1].app/application", archive_entries.stdout)

    def test_tauri_artifact_preparation_uses_custom_cargo_output(self) -> None:
        result, project = self.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        target = "aarch64-apple-darwin"
        target_directory = project / "custom target dir"
        bundle_dir = target_directory / target / "release/bundle/macos"
        app = bundle_dir / "Custom App.app"
        app.mkdir(parents=True)
        (app / "application").write_bytes(b"configured output")
        stale_dir = project / "src-tauri/target" / target / "release/bundle/macos"
        stale_app = stale_dir / "Custom App.app"
        stale_app.mkdir(parents=True)
        (stale_app / "application").write_bytes(b"stale default output")
        prepared, output = self.run_tauri_artifact_preparation(
            project, target, "macos-arm64", target_directory=target_directory
        )
        self.assertEqual(prepared.returncode, 0, prepared.stdout)
        artifact = self.uploaded_tauri_artifact(output)
        extracted = project / "extracted-custom-target"
        extracted.mkdir()
        unpacked = self.run_process(
            ["ditto", "-x", "-k", str(artifact), str(extracted)], project
        )
        self.assertEqual(unpacked.returncode, 0, unpacked.stdout)
        self.assertEqual(
            (extracted / "Custom App.app/application").read_bytes(),
            b"configured output",
        )

    def test_tauri_windows_zip_selects_only_executable(self) -> None:
        result, project = self.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        script = self.workflow_step_script(
            project, "tauri-build.yml", "Create Windows distribution ZIP"
        )
        self.assertIn('Get-ChildItem -LiteralPath $releaseDir -Filter "*.exe" -File', script)
        self.assertIn("$applications.Count -ne 1", script)
        self.assertIn("Compress-Archive -LiteralPath $applications[0].FullName", script)
        self.assertIn('$($env:ZIP_PREFIX)-$($env:ARTIFACT_PLATFORM).zip', script)
        self.assertNotIn("*.pdb", script)

    def test_tauri_macos_archive_preserves_app_permissions_and_symlinks(self) -> None:
        result, project = self.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        target = "aarch64-apple-darwin"
        app = project / f"src-tauri/target/{target}/release/bundle/macos/Custom App.app"
        executable = app / "Contents/MacOS/custom-app"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"application executable")
        executable.chmod(0o751)
        resource = app / "Contents/Resources/.hidden-icon"
        resource.parent.mkdir()
        resource.write_bytes(b"application icon")
        link = app / "Contents/Resources/icon-link"
        link.symlink_to(resource.name)
        prepared, output = self.run_tauri_artifact_preparation(
            project, target, "macos-arm64"
        )
        self.assertEqual(prepared.returncode, 0, prepared.stdout)
        artifact = self.uploaded_tauri_artifact(output)
        self.assertEqual(artifact.name, "test-project-0.1.0-macos-arm64.zip")
        extracted = project / "extracted"
        extracted.mkdir()
        unpacked = self.run_process(
            ["ditto", "-x", "-k", str(artifact), str(extracted)], project
        )
        self.assertEqual(unpacked.returncode, 0, unpacked.stdout)
        extracted_app = extracted / app.name
        actual_executable = extracted_app / executable.relative_to(app)
        self.assertEqual(actual_executable.read_bytes(), executable.read_bytes())
        self.assertEqual(actual_executable.stat().st_mode & 0o777, 0o751)
        actual_link = extracted_app / link.relative_to(app)
        self.assertTrue(actual_link.is_symlink())
        self.assertEqual(os.readlink(actual_link), resource.name)
        self.assertEqual(actual_link.read_bytes(), resource.read_bytes())

    def test_tauri_macos_artifact_supports_leading_dot_app_names(self) -> None:
        result, project = self.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true",
            "tauri_product_name=.Custom App",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        target = "aarch64-apple-darwin"
        app = project / f"src-tauri/target/{target}/release/bundle/macos/.Custom App.app"
        app.mkdir(parents=True)
        (app / "application").write_bytes(b"hidden-name application")
        prepared, output = self.run_tauri_artifact_preparation(
            project, target, "macos-arm64"
        )
        self.assertEqual(prepared.returncode, 0, prepared.stdout)
        artifact = self.uploaded_tauri_artifact(output)
        self.assertEqual(artifact.name, "test-project-0.1.0-macos-arm64.zip")
        self.assertTrue(artifact.is_file())
        archive_entries = self.run_process(
            ["unzip", "-Z", "-1", str(artifact)], project
        )
        self.assertEqual(archive_entries.returncode, 0, archive_entries.stdout)
        self.assertIn(".Custom App.app/application", archive_entries.stdout)

    def test_tauri_artifact_preparation_rejects_missing_or_ambiguous_apps(self) -> None:
        result, project = self.copy_template(
            "use_tauri=true", "use_gh_actions_tauri_build=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        target = "aarch64-apple-darwin"
        platform = "macos-arm64"
        prepared, output = self.run_tauri_artifact_preparation(
            project, target, platform
        )
        self.assertNotEqual(prepared.returncode, 0, prepared.stdout)
        self.assertIn("Expected exactly one", prepared.stdout)
        self.assertEqual(output.read_text(), "")
        bundle_dir = project / "src-tauri/target" / target / "release/bundle/macos"
        bundle_dir.mkdir(parents=True)
        for name in ("First", "Second"):
            (bundle_dir / f"{name}.app").mkdir()
        prepared, output = self.run_tauri_artifact_preparation(
            project, target, platform
        )
        self.assertNotEqual(prepared.returncode, 0, prepared.stdout)
        self.assertIn("Expected exactly one", prepared.stdout)
        self.assertEqual(output.read_text(), "")

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

    def test_tauri_package_name_defaults_to_project_and_survives_recopy(
        self,
    ) -> None:
        result, destination = self.copy_template("use_tauri=true")
        self.assertEqual(result.returncode, 0, result.stdout)

        answers_file = destination / ".copier-answers.yml"
        answers = answers_file.read_text()
        self.assertIn("tauri_package_name: test-project", answers)

        answers_file.write_text(
            answers.replace("tauri_package_name: test-project\n", "")
        )
        default_recopy = self.recopy_template(destination)
        self.assertEqual(default_recopy.returncode, 0, default_recopy.stdout)
        self.assertEqual(
            json.loads((destination / "package.json").read_text())["name"],
            "test-project",
        )

        answers_file.write_text(
            answers_file.read_text().replace(
                "tauri_package_name: test-project",
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


    def test_template_design_adr_is_not_generated(self) -> None:
        result, destination = self.copy_template("use_tauri=true")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(
            (
                destination
                / "docs/adr"
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
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        policy = json.loads((destination / '.github/release.json').read_text())
        self.assertEqual([source['path'] for source in policy['version']['sources']], ['package.json', 'src-tauri/tauri.conf.json', 'src-tauri/Cargo.toml'])

    def test_tauri_version_source_is_used_for_docker_release(self) -> None:
        result, destination = self.copy_template(
            "use_tauri=true",
            "use_docker=true",
            "use_gh_actions_docker_release=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        policy = json.loads((destination / '.github/release.json').read_text())
        self.assertEqual([source['path'] for source in policy['version']['sources']], ['package.json', 'src-tauri/tauri.conf.json', 'src-tauri/Cargo.toml'])


    def test_generated_workflows_pin_current_github_actions(self) -> None:
        configurations = {
            "python_release": (
                (
                    "use_python=true",
                    "use_gh_actions_release=true",
                ),
                {"pr-quality-checks.yml", "release-classification.yml", "release.yml"},
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
            "tauri_build": (
                ("use_tauri=true", "use_gh_actions_tauri_build=true"),
                {"tauri-quality-checks.yml", "tauri-build.yml"},
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
                if any("release" in workflow or "tauri-build" in workflow for workflow in expected_workflows):
                    expected_workflows = expected_workflows | {"release-classification.yml"}
                self.assertEqual(
                    {workflow.name for workflow in workflow_directory.glob("*.yml")},
                    expected_workflows,
                )

                for workflow_name in expected_workflows:
                    workflow = (workflow_directory / workflow_name).read_text()
                    action_lines = [
                        line
                        for line in (
                            rendered_line.strip().removeprefix("- ")
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
        self.assertIn("    runs-on: macos-latest", workflow)
        self.assertIn("enable-cache: false", workflow)
        self.assertIn("copier==9.17.1", workflow)
        self.assertIn("python -m unittest discover -s template_tests", workflow)

        result, destination = self.copy_template("use_python=false")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(
            (destination / ".github/workflows/template-quality-checks.yml").exists()
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

    def test_tauri_oxlint_config_allows_node_globals_in_config_files(self) -> None:
        result, destination = self.copy_template("use_tauri=true")

        self.assertEqual(result.returncode, 0, result.stdout)

        oxlint_config = json.loads((destination / ".oxlintrc.json").read_text())
        node_overrides = [
            override
            for override in oxlint_config["overrides"]
            if override["files"] == ["vite.config.ts", "vitest.config.ts"]
        ]

        self.assertEqual(len(node_overrides), 1)
        self.assertTrue(node_overrides[0]["env"]["node"])
