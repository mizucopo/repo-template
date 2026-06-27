import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class ChromeExtensionTemplateTest(unittest.TestCase):
    def copy_template(self, *answers: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        destination_root = tempfile.TemporaryDirectory()
        self.addCleanup(destination_root.cleanup)

        destination = Path(destination_root.name) / "project"
        command = ["copier", "copy", "--trust", "--defaults"]
        for answer in answers:
            command.extend(["-d", answer])
        command.extend([str(REPO_ROOT), str(destination)])

        result = subprocess.run(
            command,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return result, destination

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

    def test_chrome_version_source_wins_when_python_is_also_enabled(self) -> None:
        result, destination = self.copy_template(
            "use_python=true",
            "use_chrome_extension=true",
            "use_gh_actions_release=true",
            "use_gh_actions_pr_tag_check=true",
        )

        self.assertEqual(result.returncode, 0, result.stdout)

        release_workflow = (destination / ".github/workflows/release.yml").read_text()
        tag_check_workflow = (destination / ".github/workflows/pr-tag-check.yml").read_text()

        self.assertIn("package.json", release_workflow)
        self.assertIn("package.json", tag_check_workflow)

    def test_chrome_quality_workflow_fails_when_any_check_fails(self) -> None:
        result, destination = self.copy_template("use_chrome_extension=true")

        self.assertEqual(result.returncode, 0, result.stdout)

        workflow = (
            destination / ".github/workflows/chrome-extension-quality-checks.yml"
        ).read_text()

        self.assertIn('checkConclusion = "failure";', workflow)
        self.assertIn("Fail on Chrome extension quality check failure", workflow)
