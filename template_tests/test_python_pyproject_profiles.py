"""Regression tests for shared Python tooling and project-owned TOML values."""

import tomllib
import unittest
from pathlib import Path

from template_tests import test_template
import yaml


PYPROJECT_TEMPLATE = "{% if use_python %}pyproject.toml{% endif %}.jinja"


class PythonPyprojectProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)

    def render(self, *answers: str) -> Path:
        result, root = self.helper.copy_template("use_python=true", *answers)
        self.assertEqual(result.returncode, 0, result.stdout)
        return root

    @staticmethod
    def manifest(root: Path) -> dict:
        return tomllib.loads((root / "pyproject.toml").read_text())

    def test_common_development_tools_are_independent_of_legacy_stubs(self) -> None:
        baseline = self.manifest(self.render())
        minimal = self.manifest(
            self.render("python_include_legacy_stub_dependencies=false")
        )
        self.assertEqual(
            [
                requirement
                for requirement in baseline["dependency-groups"]["dev"]
                if not requirement.startswith(
                    ("python-decouple-stubs", "types-requests")
                )
            ],
            minimal["dependency-groups"]["dev"],
        )
        self.assertEqual(minimal["tool"], baseline["tool"])
        self.assertEqual(len(baseline["dependency-groups"]["dev"]), 8)
        self.assertEqual(len(minimal["dependency-groups"]["dev"]), 6)

    def test_mypy_import_root_is_an_explicit_application_choice(self) -> None:
        flat = self.manifest(self.render())
        qualified_root = self.render("python_mypy_import_root=repository")
        qualified = self.manifest(qualified_root)
        package_root = self.render("python_project_kind=package")
        package = self.manifest(package_root)
        self.assertEqual(flat["tool"]["mypy"]["mypy_path"], "src:stubs")
        self.assertEqual(qualified["tool"]["mypy"]["mypy_path"], ".:stubs")
        self.assertEqual(package["tool"]["mypy"]["mypy_path"], "src:stubs")
        self.assertEqual(
            yaml.safe_load((qualified_root / ".copier-answers.yml").read_text())[
                "python_mypy_import_root"
            ],
            "repository",
        )
        self.assertNotIn(
            "python_mypy_import_root",
            yaml.safe_load((package_root / ".copier-answers.yml").read_text()),
        )

    def customize_project(self, root: Path, kind: str) -> None:
        manifest = root / "pyproject.toml"
        text = manifest.read_text()
        text = text.replace(
            'version = "0.1.0"', 'version = "1.2.3"\nreadme = "README.md"'
        )
        text = text.replace(
            "dependencies = [\n]", 'dependencies = ["typing-extensions"]'
        )
        text = text.replace(
            "[dependency-groups]\ndev = [",
            '[dependency-groups]\ndev = [\n    "pytest-cov>=7.1.0",',
        )
        text = text.replace(
            "[tool.pytest.ini_options]\n",
            "[tool.pytest.ini_options]\n"
            'addopts = "--cov=src --cov-fail-under=80"\n'
            'markers = ["business: a project-owned domain test"]\n',
        )
        text = text.replace(
            "[tool.ruff.lint.per-file-ignores]\n",
            '[tool.ruff.lint.per-file-ignores]\n"src/**/calc.py" = ["ARG001"]\n',
        )
        if kind == "application":
            (root / "src/__init__.py").write_text("")
            source_root = root / "src"
            package = "src"
            text = text.replace(
                "[tool.uv]\npackage = false",
                '[build-system]\nrequires = ["hatchling"]\n'
                'build-backend = "hatchling.build"\n\n'
                '[tool.hatch.build.targets.wheel]\npackages = ["src"]\n\n'
                "[tool.uv]\npackage = true",
            )
        else:
            source_root = root / "src/test_project"
            package = "test_project"
        manifest.write_text(text)
        (root / "README.md").write_text("# Local project documentation\n")
        (source_root / "calc.py").write_text(
            "def answer(unused: int) -> int:\n    return 42\n"
        )
        (root / "tests/test_calc.py").write_text(
            f"import pytest\n\nfrom {package}.calc import answer\n\n\n"
            "@pytest.mark.business\ndef test_answer() -> None:\n"
            "    assert answer(0) == 42\n"
        )

    def test_existing_application_and_package_keep_local_values_on_update(self) -> None:
        for kind in ("application", "package"):
            with self.subTest(kind=kind):
                template = self.helper.copy_template_repository()
                self.helper.commit_repository(template, "initial Python template")
                options = [
                    "use_python=true",
                    f"python_project_kind={kind}",
                    "python_include_legacy_stub_dependencies=false",
                ]
                if kind == "application":
                    options.append("python_mypy_import_root=repository")
                root = self.helper.create_versioned_project(template, *options)
                self.customize_project(root, kind)

                locked = self.helper.run_process(["uv", "lock"], root)
                self.assertEqual(locked.returncode, 0, locked.stdout)
                lockfile = (root / "uv.lock").read_bytes()
                before = self.manifest(root)
                self.helper.commit_repository(root, "project manifest and lockfile")

                source = template / PYPROJECT_TEMPLATE
                source.write_text(
                    source.read_text().replace("line-length = 88", "line-length = 100")
                )
                self.helper.commit_repository(template, "shared Ruff update")

                updated = self.helper.update_versioned_project(root)
                self.assertEqual(updated.returncode, 0, updated.stdout)
                after = self.manifest(root)
                self.assertEqual(after["tool"]["ruff"]["line-length"], 100)
                before["tool"]["ruff"]["line-length"] = 100
                self.assertEqual(before, after)
                self.assertEqual((root / "uv.lock").read_bytes(), lockfile)

                checked = self.helper.run_process(
                    ["uv", "run", "--frozen", "task", "check"], root
                )
                self.assertEqual(checked.returncode, 0, checked.stdout)
                self.assertIn("Total coverage:", checked.stdout)
