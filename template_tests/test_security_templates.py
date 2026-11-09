"""Opt-in security workflows, fail-closed scanners, and Copier ownership contracts."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_template
import yaml


class SecurityTemplateTest(unittest.TestCase):
    def setUp(self):
        self.helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)

    def render(self, *answers):
        result, root = self.helper.copy_template(*answers)
        self.assertEqual(result.returncode, 0, result.stdout)
        return root

    def test_security_checks_are_independent_and_opt_in(self):
        for runtime in ("use_python=true", "use_rust=true", "use_tauri=true"):
            with self.subTest(runtime=runtime):
                root = self.render(runtime)
                for path in (
                    ".github/workflows/security-checks.yml",
                    ".bandit.yml",
                    ".github/scripts/run-bandit.py",
                    "docs/security-checks.md",
                ):
                    self.assertFalse((root / path).exists(), path)
                answers = yaml.safe_load((root / ".copier-answers.yml").read_text())
                self.assertIs(answers["use_gh_actions_bandit"], False)
                self.assertIs(answers["use_gh_actions_cargo_audit"], False)

    def test_real_bandit_accepts_reviewed_release_scripts_but_rejects_findings(self):
        cases = (
            ["use_gh_actions_release=true"],
            ["use_docker=true", "use_gh_actions_docker_release=true"],
            [
                "use_docker=true",
                "use_gh_actions_docker_release=true",
                "use_aws_ecr=true",
            ],
        )
        for answers in cases:
            with self.subTest(answers=answers):
                root = self.render(
                    "use_python=true", "use_gh_actions_bandit=true", *answers
                )
                python = os.environ.get("BANDIT_PYTHON", sys.executable)

                def scan(python=python, root=root):
                    return subprocess.run(
                        [python, "-I", ".github/scripts/run-bandit.py"],
                        cwd=root,
                        check=False,
                        capture_output=True,
                        text=True,
                    )

                clean = scan()
                self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
                self.assertNotIn("not a test name or id", clean.stderr)
                self.assertIn("2 Python files scanned", clean.stdout)
                # A reviewed shared script must not exclude new project findings.
                (root / "src/unsafe.py").write_text(
                    "import pickle\npickle.loads(b'unsafe')\n"
                )
                finding = scan()
                self.assertNotEqual(finding.returncode, 0)
                self.assertIn("B301", finding.stdout + finding.stderr)

    def test_release_api_rejects_non_https_or_credential_urls_before_request(self):
        root = self.render("use_gh_actions_release=true")
        spec = importlib.util.spec_from_file_location(
            "security_release", root / ".github/scripts/release.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for endpoint in (
            "file:///etc/passwd",
            "http://api.example.test",
            "https:///missing-host",
            "https://user:password@api.example.test",
        ):
            for path in ("/repos/owner/project", "/graphql"):
                with (
                    self.subTest(endpoint=endpoint, path=path),
                    mock.patch.dict(
                        os.environ,
                        {
                            "GITHUB_API_URL": endpoint,
                            "GITHUB_GRAPHQL_URL": endpoint,
                        },
                    ),
                    mock.patch.object(module.request, "urlopen") as opened,
                ):
                    client = module.GitHub("owner/project")
                    with self.assertRaisesRegex(module.PreparationError, "HTTPS"):
                        client.api(path)
                    opened.assert_not_called()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_API_URL": "https://git.example.test/api/v3",
                    "GH_TOKEN": "fixture",
                },
            ),
            mock.patch.object(module.request, "urlopen") as opened,
        ):
            opened.return_value.__enter__.return_value.read.return_value = b"{}"
            self.assertEqual(
                module.GitHub("owner/project").api("/repos/owner/project"), {}
            )
            self.assertEqual(
                opened.call_args.args[0].full_url,
                "https://git.example.test/api/v3/repos/owner/project",
            )

    def test_enabled_combinations_are_secretless_valid_workflows(self):
        cases = [
            (["use_python=true", "use_gh_actions_bandit=true"], {"bandit"}, "."),
            (
                ["use_rust=true", "use_gh_actions_cargo_audit=true"],
                {"cargo-audit"},
                ".",
            ),
            (
                ["use_tauri=true", "use_gh_actions_cargo_audit=true"],
                {"cargo-audit"},
                "src-tauri",
            ),
            (
                [
                    "use_python=true",
                    "use_rust=true",
                    "use_gh_actions_bandit=true",
                    "use_gh_actions_cargo_audit=true",
                    "use_gh_actions_codeql=true",
                ],
                {"bandit", "cargo-audit"},
                ".",
            ),
            (
                [
                    "use_python=true",
                    "use_tauri=true",
                    "use_gh_actions_bandit=true",
                    "use_gh_actions_cargo_audit=true",
                ],
                {"bandit", "cargo-audit"},
                "src-tauri",
            ),
        ]
        for answers, expected, directory in cases:
            with self.subTest(answers=answers):
                root = self.render(*answers)
                path = root / ".github/workflows/security-checks.yml"
                text = path.read_text()
                workflow = yaml.safe_load(text)
                self.assertEqual(workflow["permissions"], {"contents": "read"})
                triggers = workflow[True]
                for trigger in ("push", "pull_request"):
                    self.assertEqual(triggers[trigger], {"branches": ["main"]})
                self.assertIn("schedule", triggers)
                self.assertIn("workflow_dispatch", triggers)
                self.assertNotIn("pull_request_target", triggers)
                self.assertEqual(set(workflow["jobs"]), expected)
                self.assertNotIn("secrets.", text)
                self.assertNotIn("continue-on-error", text)
                for job in workflow["jobs"].values():
                    self.assertEqual(job["runs-on"], "ubuntu-latest")
                    self.assertGreater(job["timeout-minutes"], 0)
                    self.assertNotIn("permissions", job)
                    for step in job["steps"]:
                        if "uses" in step:
                            self.assertRegex(step["uses"], r"^[^@]+@[0-9a-f]{40}$")
                            if step["uses"].startswith("actions/checkout@"):
                                self.assertIs(
                                    step["with"]["persist-credentials"], False
                                )
                if "bandit" in expected:
                    self.assertIn(
                        "uv run --isolated --no-project --with bandit==1.9.4", text
                    )
                    self.assertIn("python -I .github/scripts/run-bandit.py", text)
                    self.assertTrue((root / ".bandit.yml").is_file())
                if "cargo-audit" in expected:
                    job = workflow["jobs"]["cargo-audit"]
                    self.assertEqual(
                        job["defaults"]["run"]["working-directory"], directory
                    )
                    self.assertIn(
                        "git ls-files --error-unmatch Cargo.lock && test -s Cargo.lock",
                        text,
                    )
                    installer = next(
                        step
                        for step in job["steps"]
                        if step["name"] == "Install cargo-audit"
                    )
                    self.assertEqual(
                        installer["working-directory"], "${{ runner.temp }}"
                    )
                    self.assertEqual(
                        installer["run"],
                        "cargo install cargo-audit --version 0.22.2 --locked "
                        '--root "$RUNNER_TEMP/security-tools"',
                    )
                    audit = next(
                        step
                        for step in job["steps"]
                        if step["name"] == "Audit Rust dependencies"
                    )
                    self.assertEqual(
                        audit["run"],
                        '"$RUNNER_TEMP/security-tools/bin/cargo-audit" '
                        "audit --file Cargo.lock --no-yanked",
                    )
                    self.assertNotIn("working-directory", audit)
                    self.assertNotIn("cargo +stable", text)
                if "use_gh_actions_codeql=true" in answers:
                    self.assertTrue((root / ".github/workflows/codeql.yml").is_file())
                linted = subprocess.run(
                    ["actionlint", str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(linted.returncode, 0, linted.stdout + linted.stderr)

    def test_lockfile_preflight_rejects_missing_untracked_and_empty_locks(self):
        for runtime, directory in (
            ("use_rust=true", "."),
            ("use_tauri=true", "src-tauri"),
        ):
            with self.subTest(runtime=runtime):
                root = self.render(runtime, "use_gh_actions_cargo_audit=true")
                workflow = yaml.safe_load(
                    (root / ".github/workflows/security-checks.yml").read_text()
                )
                steps = workflow["jobs"]["cargo-audit"]["steps"]
                command = next(
                    step["run"]
                    for step in steps
                    if step["name"] == "Require committed lockfile"
                )
                self.helper.commit_repository(root, "generated fixture")
                cwd = root / directory
                lock = cwd / "Cargo.lock"
                if lock.exists():
                    lock.unlink()

                def preflight(command=command, cwd=cwd):
                    return subprocess.run(
                        ["sh", "-c", command],
                        cwd=cwd,
                        check=False,
                        capture_output=True,
                        text=True,
                    )

                self.assertNotEqual(preflight().returncode, 0)
                lock.write_text("version = 3\n")
                self.assertNotEqual(preflight().returncode, 0)
                self.helper.commit_repository(root, "track lockfile")
                self.assertEqual(preflight().returncode, 0)
                lock.write_text("")
                self.assertNotEqual(preflight().returncode, 0)

    def test_generated_security_files_are_prettier_formatted(self):
        cases = [
            ("use_python=true", "use_gh_actions_bandit=true"),
            ("use_rust=true", "use_gh_actions_cargo_audit=true"),
            (
                "use_python=true",
                "use_rust=true",
                "use_gh_actions_bandit=true",
                "use_gh_actions_cargo_audit=true",
            ),
            ("use_tauri=true", "use_gh_actions_cargo_audit=true"),
            (
                "use_python=true",
                "use_tauri=true",
                "use_gh_actions_bandit=true",
                "use_gh_actions_cargo_audit=true",
            ),
        ]
        with tempfile.TemporaryDirectory() as tools_directory:
            tools = Path(tools_directory)
            installed = subprocess.run(
                [
                    "npm",
                    "install",
                    "--no-audit",
                    "--no-fund",
                    "--ignore-scripts",
                    "--prefix",
                    str(tools),
                    "prettier@3.9.6",
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "npm_config_cache": str(test_template.NPM_CACHE)},
            )
            self.assertEqual(
                installed.returncode, 0, installed.stdout + installed.stderr
            )
            prettier = str(tools / "node_modules/.bin/prettier")
            for answers in cases:
                with self.subTest(answers=answers):
                    root = self.render(*answers)
                    paths = [
                        ".github/workflows/security-checks.yml",
                        "docs/security-checks.md",
                    ]
                    if (root / ".bandit.yml").exists():
                        paths.append(".bandit.yml")
                    if "use_tauri=true" in answers:
                        # Tauri's format:check checks the entire generated project.
                        paths = ["."]
                    checked = subprocess.run(
                        [prettier, "--check", *paths],
                        cwd=root,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        checked.returncode, 0, checked.stdout + checked.stderr
                    )

    def test_runtime_validation(self):
        for answers, message in [
            (("use_python=false", "use_gh_actions_bandit=true"), "use_python=true"),
            (
                (
                    "use_rust=false",
                    "use_tauri=false",
                    "use_gh_actions_cargo_audit=true",
                ),
                "use_rust=true",
            ),
        ]:
            with self.subTest(answers=answers):
                result, _ = self.helper.copy_template(*answers)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout)

    def test_copier_update_toggle_preserves_project_sources_and_custom_config(self):
        template = self.helper.copy_template_repository()
        self.helper.commit_repository(template, "template baseline")
        project = self.helper.create_versioned_project(
            template, "use_python=true", "use_rust=true"
        )
        module = project / "src/config.py"
        module.write_text("PROJECT_SETTING = 'preserved'\n")
        self.helper.commit_repository(project, "custom source")
        result = self.helper.update_versioned_project(
            project, "use_gh_actions_bandit=true", "use_gh_actions_cargo_audit=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        config = project / ".bandit.yml"
        custom = "exclude_dirs: ['src/generated']\nskips: []\n"
        config.write_text(custom)
        self.helper.commit_repository(project, "enable and customize security")
        result = self.helper.update_versioned_project(project)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(config.read_text(), custom)
        result = self.helper.update_versioned_project(
            project, "use_gh_actions_bandit=false", "use_gh_actions_cargo_audit=false"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((project / ".github/workflows/security-checks.yml").exists())
        self.assertFalse((project / ".github/scripts/run-bandit.py").exists())
        self.assertEqual(module.read_text(), "PROJECT_SETTING = 'preserved'\n")


class BanditRunnerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "src").mkdir()
        scripts = self.root / ".github/scripts"
        scripts.mkdir(parents=True)
        source = (
            test_template.REPO_ROOT
            / ".github/{% if use_gh_actions_bandit %}scripts{% endif %}/run-bandit.py"
        )
        self.runner = scripts / "run-bandit.py"
        shutil.copyfile(source, self.runner)
        self.config = self.root / ".bandit.yml"
        self.config.write_text("exclude_dirs: []\nskips: []\n")
        spec = importlib.util.spec_from_file_location(
            "security_test_runner", self.runner
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def run_bandit(self):
        python = os.environ.get("BANDIT_PYTHON", sys.executable)
        version = subprocess.run(
            [python, "-m", "bandit", "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertRegex(version.stdout.splitlines()[0], r"\b1\.9\.4$")
        return subprocess.run(
            [python, "-I", str(self.runner)],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_real_bandit_clean_and_empty_src(self):
        for content in (None, "VALUE = 42\n"):
            with self.subTest(content=content):
                if content:
                    (self.root / "src/clean.py").write_text(content)
                result = self.run_bandit()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Python files scanned", result.stdout)

    def test_real_bandit_large_scan_suppresses_progress_in_json(self):
        # Bandit 1.9.4 enables rich progress output above 50 discovered files.
        for index in range(51):
            (self.root / f"src/safe_{index}.py").write_text(f"VALUE = {index}\n")
        python = os.environ.get("BANDIT_PYTHON", sys.executable)
        command = [
            python,
            "-I",
            "-m",
            "bandit",
            "--recursive",
            "src",
            ".github/scripts",
            "--exclude",
            "",
            "--configfile",
            str(self.config),
            "--format",
            "json",
        ]
        noisy = subprocess.run(
            command, cwd=self.root, check=False, capture_output=True, text=True
        )
        self.assertEqual(noisy.returncode, 0, noisy.stdout + noisy.stderr)
        self.assertIn("Working...", noisy.stdout)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(noisy.stdout)
        quiet = subprocess.run(
            [*command, "--quiet"],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(quiet.returncode, 0, quiet.stdout + quiet.stderr)
        report = json.loads(quiet.stdout)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["results"], [])
        self.assertEqual(
            len([name for name in report["metrics"] if name != "_totals"]), 52
        )
        result = self.run_bandit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("52 Python files scanned", result.stdout)
        for source, diagnostic in (
            ("import pickle\npickle.loads(b'unsafe')\n", "B301"),
            ("def broken(:\n", "Bandit scan error"),
        ):
            with self.subTest(source=source):
                (self.root / "src/safe_0.py").write_text(source)
                failed = self.run_bandit()
                self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
                self.assertIn(diagnostic, failed.stdout + failed.stderr)

    def test_real_bandit_vulnerability_and_syntax_error_fail(self):
        for source in ("import pickle\npickle.loads(b'unsafe')\n", "def broken(:\n"):
            with self.subTest(source=source):
                (self.root / "src/check.py").write_text(source)
                result = self.run_bandit()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_bandit_scans_nested_github_scripts(self):
        (self.root / "src/clean.py").write_text("VALUE = 42\n")
        nested = self.root / ".github/scripts/nested"
        nested.mkdir()
        (nested / "unsafe.py").write_text("import pickle\npickle.loads(b'unsafe')\n")
        result = self.run_bandit()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unsafe.py", result.stdout + result.stderr)

    def test_real_bandit_missing_target_and_config_fail(self):
        (self.root / "src").rmdir()
        result = self.run_bandit()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("target directory is missing", result.stderr)
        (self.root / "src").mkdir()
        self.config.unlink()
        result = self.run_bandit()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("configuration is missing", result.stderr)

    def test_real_bandit_invalid_config_and_narrow_exclusion(self):
        self.config.write_text("exclude_dirs: [\n")
        self.assertNotEqual(self.run_bandit().returncode, 0)
        generated = self.root / "src/generated"
        generated.mkdir()
        (generated / "unsafe.py").write_text("import pickle\npickle.loads(b'unsafe')\n")
        self.config.write_text("exclude_dirs: ['src/generated']\nskips: []\n")
        result = self.run_bandit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.root / "src/unsafe.py").write_text(
            "import pickle\npickle.loads(b'unsafe')\n"
        )
        self.assertNotEqual(self.run_bandit().returncode, 0)

    def test_real_bandit_never_imports_project_shadow_modules(self):
        marker = self.root / "executed-marker"
        source = (
            "from pathlib import Path\n"
            "(Path.cwd() / 'executed-marker').write_text('executed')\n"
        )
        (self.root / "bandit.py").write_text(source)
        (self.root / ".github/scripts/json.py").write_text(source)
        result = self.run_bandit()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists())

    def test_unreadable_directory_fails_before_scanning(self):
        with (
            mock.patch.object(
                self.module.os, "walk", side_effect=PermissionError("unreadable")
            ),
            mock.patch.object(self.module.subprocess, "run") as process,
        ):
            self.assertNotEqual(self.module.main(), 0)
            process.assert_not_called()
        blocked = self.root / "src/blocked"
        blocked.mkdir()
        (blocked / "unsafe.py").write_text("import pickle\npickle.loads(b'unsafe')\n")
        blocked.chmod(0)
        try:
            result = self.run_bandit()
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            # Root may bypass chmod; the deterministic mocked failure is mandatory.
            if os.geteuid() != 0:
                self.assertIn("Could not traverse Bandit targets", result.stderr)
        finally:
            blocked.chmod(0o700)

    def test_report_and_process_fail_closed(self):
        clean = {
            "errors": [],
            "results": [],
            "metrics": {"src/clean.py": {}, "_totals": {}},
        }
        cases = [
            ("not json", 0, ""),
            ("[]", 0, ""),
            ("{}", 0, ""),
            (json.dumps(clean), 2, ""),
            (json.dumps(clean), 0, "[tester] ERROR internal failure\n"),
            (json.dumps({**clean, "errors": ["syntax error"]}), 0, ""),
            (json.dumps({**clean, "results": [{}]}), 0, ""),
            (
                json.dumps(
                    {
                        **clean,
                        "results": [
                            {
                                "filename": "src/unsafe.py",
                                "line_number": 1,
                                "test_id": "B301",
                                "issue_severity": "MEDIUM",
                                "issue_confidence": "HIGH",
                                "issue_text": "synthetic finding",
                            }
                        ],
                    }
                ),
                0,
                "",
            ),
            (json.dumps({**clean, "errors": {}}), 0, ""),
            (json.dumps({**clean, "metrics": []}), 0, ""),
            (json.dumps({**clean, "metrics": {"_totals": {}}}), 0, ""),
        ]
        for stdout, code, stderr in cases:
            with self.subTest(stdout=stdout, code=code, stderr=stderr):
                result = subprocess.CompletedProcess([], code, stdout, stderr)
                with mock.patch.object(
                    self.module.subprocess, "run", return_value=result
                ):
                    self.assertNotEqual(self.module.main(), 0)
        with mock.patch.object(
            self.module.subprocess, "run", side_effect=OSError("cannot execute")
        ):
            self.assertNotEqual(self.module.main(), 0)
        with mock.patch.object(
            self.module.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(clean), ""),
        ) as process:
            self.assertEqual(self.module.main(), 0)
            argv = process.call_args.args[0]
            self.assertEqual(
                argv[:7],
                [sys.executable, "-I", "-m", "bandit", "--quiet", "--recursive", "src"],
            )
            self.assertEqual(argv[7], ".github/scripts")
            self.assertIn("--quiet", argv)
            exclude_index = argv.index("--exclude")
            self.assertEqual(argv[exclude_index + 1], "")
            self.assertEqual(process.call_args.kwargs["cwd"], self.root)
            self.assertFalse(process.call_args.kwargs.get("shell", False))


class CargoAuditRuntimeTest(unittest.TestCase):
    """Exercise the pinned binary without registry or RustSec network access."""

    def setUp(self):
        self.binary = os.environ.get("CARGO_AUDIT", "cargo-audit")
        version = subprocess.run(
            [self.binary, "--version"], check=False, capture_output=True, text=True
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn("0.22.2", version.stdout)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.database = self.root / "advisory-db"
        advisory = self.database / "crates/security-fixture/RUSTSEC-2026-0001.md"
        advisory.parent.mkdir(parents=True)
        advisory.write_text("""```toml
[advisory]
id = "RUSTSEC-2026-0001"
package = "security-fixture"
date = "2026-01-01"
url = "https://example.com/security-fixture"
categories = ["memory-corruption"]

[versions]
patched = [">= 1.0.1"]
```

# Synthetic test advisory

This is an offline regression fixture, not a real advisory.
""")
        self.helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)
        self.helper.commit_repository(self.database, "offline advisory fixture")

    def write_lock(self, version):
        lock = self.root / "Cargo.lock"
        lock.write_text(f'''version = 3

[[package]]
name = "security-fixture"
version = "{version}"
source = "registry+https://github.com/rust-lang/crates.io-index"
''')
        return lock

    def audit(self, *extra):
        return subprocess.run(
            [
                self.binary,
                "audit",
                "--file",
                "Cargo.lock",
                "--no-yanked",
                "--db",
                str(self.database),
                "--no-fetch",
                *extra,
            ],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_clean_and_vulnerable_locks(self):
        self.write_lock("1.0.1")
        clean = self.audit()
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        self.write_lock("1.0.0")
        vulnerable = self.audit()
        self.assertNotEqual(vulnerable.returncode, 0)
        self.assertIn("RUSTSEC-2026-0001", vulnerable.stdout + vulnerable.stderr)

    def test_missing_and_malformed_locks_fail(self):
        self.assertNotEqual(self.audit().returncode, 0)
        (self.root / "Cargo.lock").write_text("[[package]\nmalformed\n")
        self.assertNotEqual(self.audit().returncode, 0)

    def test_unavailable_advisory_database_fails(self):
        self.write_lock("1.0.1")
        result = subprocess.run(
            [
                self.binary,
                "audit",
                "--file",
                "Cargo.lock",
                "--no-yanked",
                "--db",
                str(self.root / "missing-db"),
                "--url",
                (self.root / "missing-upstream.git").as_uri(),
            ],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
