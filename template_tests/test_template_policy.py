import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POLICY = ".github/repo-template-policy.json"
WORKFLOW = ".github/workflows/repo-template-policy.yml"
CHECKER = ".github/scripts/check-template-policy.py"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                   stderr=subprocess.STDOUT, timeout=30)


def record(repo, message):
    git(repo, "add", "--all")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", message)


class TemplatePolicyTests(unittest.TestCase):
    def test_real_copier_render_and_candidate_diff(self):
        self.assertIsNotNone(shutil.which("actionlint"), "actionlint required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, head, canonical = (root / name for name in ("base", "head", "canonical"))
            git(root, "clone", "--quiet", "--no-local", str(REPO), str(canonical))
            subprocess.run(
                [sys.executable, "-m", "copier", "copy", "--trust", "--skip-tasks",
                 "--defaults", "--vcs-ref", "HEAD", "-d", "use_python=true",
                 "-d", "use_repo_template_policy=true", str(canonical), str(base)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True, timeout=120,
            )
            lint = subprocess.run(["actionlint", str(base / WORKFLOW)], cwd=base, text=True,
                                  capture_output=True, timeout=30)
            self.assertEqual(0, lint.returncode, lint.stdout + lint.stderr)
            self.assertIn("persist-credentials: false", (base / WORKFLOW).read_text())
            answers = base / ".copier-answers.yml"
            content = answers.read_text().replace(str(canonical), "gh:mizucopo/repo-template")
            answers.write_text(content)
            git(base, "init", "--quiet")
            record(base, "Generated application")
            git(root, "clone", "--quiet", str(base), str(head))
            original = git(head, "rev-parse", "HEAD").strip()

            def verify(success, message=""):
                result = subprocess.run(
                    [sys.executable, str(base / CHECKER), "--base", str(base),
                     "--head", str(head), "--template", str(canonical)],
                    text=True, capture_output=True, timeout=240,
                    env={**os.environ, "COPIER_CACHE_DIR": str(root / "copier-cache")},
                )
                output = result.stdout + result.stderr
                self.assertEqual(success, result.returncode == 0, output)
                self.assertIn(message, output)
                return output

            verify(True)
            project = head / "pyproject.toml"
            project.write_text(project.read_text().replace('description = "Test Project"',
                                                           'description = "Local behavior"'))
            record(head, "Local project data")
            verify(True)

            project.write_text(project.read_text().replace("line-length = 88", "line-length = 77"))
            (head / POLICY).write_text(json.dumps(
                {"local_workflows": [], "local_managed_keys": ["pyproject.toml:tool.ruff.line-length"]}
            ))
            record(head, "Attempt unilateral checked-key exception")
            verify(False, "pyproject.toml:tool.ruff.line-length")
            git(head, "reset", "--hard", original)

            custom = head / ".github/workflows/product.yml"
            custom.write_text("name: Product validation\non: push\njobs: {}\n")
            record(head, "Add a local workflow")
            verify(False, ".github/workflows/product.yml")
            (base / POLICY).write_text(json.dumps(
                {"local_workflows": [".github/workflows/product.yml"], "local_managed_keys": []}
            ))
            record(base, "Previously approved explicit product workflow")
            verify(True)

            managed = head / ".github/workflows/pr-quality-checks.yml"
            managed.write_text(managed.read_text() + "\n# local duplicate step\n")
            record(head, "Modify managed workflow")
            verify(False, ".github/workflows/pr-quality-checks.yml")
            git(head, "reset", "--hard", original)
            (head / WORKFLOW).unlink()
            record(head, "Remove managed guard")
            verify(False, WORKFLOW)

            git(head, "reset", "--hard", original)
            template_path = next((canonical / ".github" / "workflows").glob(
                "*pr-quality-checks.yml*"
            ))
            note = "\n# Shared upstream change\n"
            template_path.write_text(template_path.read_text() + note)
            record(canonical, "Update the canonical workflow")
            canonical_sha = git(canonical, "rev-parse", "HEAD").strip()
            (head / ".copier-answers.yml").write_text(
                "\n".join(
                    f"_commit: {canonical_sha}" if line.startswith("_commit:") else line
                    for line in content.splitlines()
                ) + "\n"
            )
            managed.write_text(managed.read_text() + note)
            record(head, "Apply the canonical revision")
            verify(True)
