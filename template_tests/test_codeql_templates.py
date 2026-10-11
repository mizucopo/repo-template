import json
import subprocess
import unittest

import test_template
import yaml


class CodeQLTemplateTest(unittest.TestCase):
    def setUp(self):
        self.helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)

    def render(self, *answers):
        result, root = self.helper.copy_template(*answers)
        self.assertEqual(result.returncode, 0, result.stdout)
        return root

    def test_codeql_is_opt_in(self):
        root = self.render("use_python=true")
        self.assertFalse((root / ".github/workflows/codeql.yml").exists())
        self.assertFalse((root / "docs/codeql.md").exists())
        answers = yaml.safe_load((root / ".copier-answers.yml").read_text())
        self.assertIs(answers["use_gh_actions_codeql"], False)
        self.assertNotIn("codeql_languages", answers)

    def test_actions_only_codeql_tracks_its_action_dependencies(self):
        root = self.render("use_gh_actions_codeql=true")
        workflow = yaml.safe_load((root / ".github/workflows/codeql.yml").read_text())
        self.assertEqual(
            workflow["jobs"]["analyze"]["strategy"]["matrix"]["language"],
            ["actions"],
        )
        updates = yaml.safe_load((root / ".github/dependabot.yml").read_text())["updates"]
        self.assertEqual(
            [(u["package-ecosystem"], u["directory"]) for u in updates],
            [("github-actions", "/")],
        )

    def test_selected_languages_have_independent_secretless_analysis(self):
        cases = [
            ("use_python=true", ["actions", "python"]),
            ("use_rust=true", ["actions", "python", "rust"]),
            ("use_chrome_extension=true", ["actions", "javascript-typescript"]),
            ("use_tauri=true", ["actions", "javascript-typescript", "rust"]),
            ("use_python=true", ["actions", "python", "ruby"]),
        ]
        for runtime, languages in cases:
            with self.subTest(runtime=runtime, languages=languages):
                root = self.render(
                    runtime,
                    "use_gh_actions_codeql=true",
                    "codeql_languages=" + json.dumps(languages),
                )
                source = root / ".github/workflows/codeql.yml"
                text = source.read_text()
                workflow = yaml.safe_load(text)
                triggers = workflow[True]  # PyYAML's YAML 1.1 loader parses `on` as True.
                self.assertEqual(triggers["pull_request"], {"branches": ["main"]})
                self.assertEqual(triggers["push"], {"branches": ["main"]})
                self.assertEqual(triggers["schedule"], [{"cron": "17 3 * * 1"}])
                self.assertIn("workflow_dispatch", triggers)
                self.assertNotIn("pull_request_target", triggers)
                job = workflow["jobs"]["analyze"]
                self.assertEqual(job["runs-on"], "ubuntu-latest")
                self.assertEqual(
                    job["permissions"],
                    {"contents": "read", "security-events": "write"},
                )
                self.assertIs(job["strategy"]["fail-fast"], False)
                self.assertEqual(job["strategy"]["matrix"]["language"], languages)
                checkout, init, analyze = job["steps"]
                self.assertIs(checkout["with"]["persist-credentials"], False)
                self.assertEqual(
                    init["with"],
                    {"languages": "${{ matrix.language }}", "build-mode": "none"},
                )
                self.assertEqual(
                    analyze["with"]["category"], "/language:${{ matrix.language }}"
                )
                self.assertNotIn("secrets.", text)
                self.assertTrue(all("run" not in s for s in job["steps"]))
                for step in job["steps"]:
                    self.assertRegex(step["uses"], r"^[^@]+@[0-9a-f]{40}$")
                linted = subprocess.run(
                    ["actionlint", str(source)], capture_output=True, text=True
                )
                self.assertEqual(linted.returncode, 0, linted.stdout + linted.stderr)

    def test_codeql_requires_at_least_one_supported_language(self):
        result, _ = self.helper.copy_template(
            "use_gh_actions_codeql=true", "codeql_languages=[]"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("解析言語を1つ以上", result.stdout)
        result, _ = self.helper.copy_template(
            "use_gh_actions_codeql=true", 'codeql_languages=["shell"]'
        )
        self.assertNotEqual(result.returncode, 0)

    def test_copier_update_enables_and_removes_codeql_without_changing_project_code(self):
        template = self.helper.copy_template_repository()
        self.helper.commit_repository(template, "template baseline")
        project = self.helper.create_versioned_project(template, "use_python=true")
        module = project / "src/config.py"
        module.write_text("PROJECT_SETTING = 'preserved'\n")
        self.helper.commit_repository(project, "project settings")
        updated = self.helper.update_versioned_project(project)
        self.assertEqual(updated.returncode, 0, updated.stdout)
        self.assertFalse((project / ".github/workflows/codeql.yml").exists())
        enabled = self.helper.update_versioned_project(
            project,
            "use_gh_actions_codeql=true",
            'codeql_languages=["actions","python"]',
        )
        self.assertEqual(enabled.returncode, 0, enabled.stdout)
        self.assertTrue((project / ".github/workflows/codeql.yml").is_file())
        self.assertEqual(module.read_text(), "PROJECT_SETTING = 'preserved'\n")
        self.helper.commit_repository(project, "enable codeql")
        disabled = self.helper.update_versioned_project(
            project, "use_gh_actions_codeql=false"
        )
        self.assertEqual(disabled.returncode, 0, disabled.stdout)
        self.assertFalse((project / ".github/workflows/codeql.yml").exists())
        self.assertFalse((project / "docs/codeql.md").exists())
        self.assertEqual(module.read_text(), "PROJECT_SETTING = 'preserved'\n")
