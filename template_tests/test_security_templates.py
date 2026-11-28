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
                    ".github/scripts/run-cargo-audit.py",
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

    def test_bandit_and_template_policy_work_together(self):
        root = self.render(
            "use_python=true",
            "use_gh_actions_bandit=true",
            "use_repo_template_policy=true",
        )
        self.assertTrue((root / ".github/scripts/check-template-policy.py").is_file())
        scan = subprocess.run(
            [os.environ.get("BANDIT_PYTHON", sys.executable), "-I",
             ".github/scripts/run-bandit.py"],
            cwd=root, check=False, capture_output=True, text=True,
        )
        self.assertEqual(scan.returncode, 0, scan.stdout + scan.stderr)
        self.assertIn("2 Python files scanned", scan.stdout)

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
                expected_crons = ["37 3 * * 1"]
                if "cargo-audit" in expected:
                    if "bandit" in expected:
                        expected_crons.append("37 3 * * 0,2-6")
                        self.assertEqual(
                            workflow["jobs"]["bandit"]["if"],
                            "github.event_name != 'schedule' || "
                            "github.event.schedule == '37 3 * * 1'",
                        )
                    else:
                        expected_crons = ["37 3 * * *"]
                    notify = workflow["jobs"]["notify-rust-audit"]
                    self.assertEqual(notify["needs"], "cargo-audit")
                    self.assertEqual(notify["permissions"], {"issues": "write"})
                    self.assertEqual(
                        notify["if"],
                        "${{ !cancelled() && github.event_name == 'schedule' && "
                        "needs.cargo-audit.outputs.fingerprint != '' }}",
                    )
                    self.assertNotIn("uses", notify["steps"][0])
                    expected = expected | {"notify-rust-audit"}
                self.assertEqual(
                    triggers["schedule"], [{"cron": cron} for cron in expected_crons]
                )
                self.assertIn("workflow_dispatch", triggers)
                self.assertNotIn("pull_request_target", triggers)
                self.assertEqual(set(workflow["jobs"]), expected)
                self.assertNotIn("secrets.", text)
                self.assertNotIn("continue-on-error", text)
                for job_id, job in workflow["jobs"].items():
                    self.assertEqual(job["runs-on"], "ubuntu-latest")
                    self.assertGreater(job["timeout-minutes"], 0)
                    if job_id != "notify-rust-audit":
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
                    self.assertTrue(
                        (root / ".github/scripts/run-cargo-audit.py").is_file()
                    )
                    self.assertEqual(
                        job["steps"][0]["with"]["fetch-depth"],
                        "${{ github.event_name == 'pull_request' && 2 || 1 }}",
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
                        'python3 -I "$GITHUB_WORKSPACE/.github/scripts/run-cargo-audit.py" '
                        '"$RUNNER_TEMP/security-tools/bin/cargo-audit" '
                        f"{(Path(directory) / 'Cargo.lock').as_posix()}",
                    )
                    self.assertEqual(
                        audit["env"]["AUDIT_BASE_SHA"],
                        "${{ github.event.pull_request.base.sha }}",
                    )
                    self.assertEqual(
                        job["outputs"]["fingerprint"],
                        "${{ steps.audit.outputs.fingerprint }}",
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

    def test_scheduled_notification_opens_an_issue_only_if_needed(self):
        root = self.render("use_rust=true", "use_gh_actions_cargo_audit=true")
        workflow = yaml.safe_load(
            (root / ".github/workflows/security-checks.yml").read_text()
        )
        command = workflow["jobs"]["notify-rust-audit"]["steps"][0]["run"]
        gh = root / "gh"
        gh.write_text("""#!/bin/bash
case "$1" in
  api) printf '%s' "$OPEN_ISSUES"; exit "$LOOKUP_STATUS" ;;
  issue) printf '%s\n' "$@" > "$CREATED_ISSUE" ;;
  *) exit 1 ;;
esac
""")
        gh.chmod(0o755)
        created = root / "created-issue"
        fingerprint = "a" * 64
        for prior, status in ((fingerprint, 0), ("", 1), ("b" * 64, 0)):
            with self.subTest(prior=prior, status=status):
                issues = [
                    {
                        "number": 42,
                        "user": {"type": "Bot"},
                        "body": f"<!-- scheduled-rust-audit:{prior} -->",
                    }
                ]
                result = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", command],
                    cwd=root,
                    env={
                        **os.environ,
                        "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                        "GITHUB_REPOSITORY": "owner/project",
                        "GITHUB_SERVER_URL": "https://github.com",
                        "GITHUB_RUN_ID": "123",
                        "AUDIT_ASSIGNEE": "maintainer",
                        "AUDIT_FINGERPRINT": fingerprint,
                        "OPEN_ISSUES": json.dumps(issues),
                        "LOOKUP_STATUS": str(status),
                        "CREATED_ISSUE": str(created),
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, status, result.stderr)
                if prior == fingerprint or status:
                    self.assertFalse(created.exists())
                else:
                    arguments = created.read_text()
                    self.assertIn("--assignee=maintainer", arguments)
                    self.assertIn("owner/project/actions/runs/123", arguments)
                    self.assertIn(f"<!-- scheduled-rust-audit:{fingerprint} -->", arguments)

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
        self.assertFalse((project / ".github/scripts/run-cargo-audit.py").exists())
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
        advisory.with_name("RUSTSEC-2026-0002.md").write_text("""```toml
[advisory]
id = "RUSTSEC-2026-0002"
package = "security-fixture"
date = "2026-01-01"
informational = "unmaintained"

[versions]
patched = []
```

# Synthetic maintenance advisory

This is an offline regression fixture, not a real advisory.
""")
        self.helper = test_template.TemplateTest(methodName="runTest")
        self.addCleanup(self.helper.doCleanups)
        self.helper.commit_repository(self.database, "offline advisory fixture")

    def write_lock(self, version, workdir=None):
        lock = (workdir or self.root) / "Cargo.lock"
        lock.write_text(f'''version = 3

[[package]]
name = "security-fixture"
version = "{version}"
source = "registry+https://github.com/rust-lang/crates.io-index"
''')
        return lock

    def generated_gate(self, runtime):
        result, project = self.helper.copy_template(
            f"use_{runtime}=true", "use_gh_actions_cargo_audit=true"
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        workdir = project / "src-tauri" if runtime == "tauri" else project
        config = workdir / ".cargo/audit.toml"
        config.parent.mkdir(exist_ok=True)
        config.write_text(
            '[database]\n'
            f'path = "{self.database}"\n'
            'fetch = false\n'
            'stale = false\n'
        )
        self.helper.commit_repository(project, "generated audit fixture")
        return project, workdir

    def run_gate(self, project, workdir, base="", event="pull_request", summary=None):
        workflow = yaml.safe_load(
            (project / ".github/workflows/security-checks.yml").read_text()
        )
        step = next(
            step for step in workflow["jobs"]["cargo-audit"]["steps"]
            if step.get("id") == "audit"
        )
        tools = self.root / "security-tools/bin"
        tools.mkdir(parents=True, exist_ok=True)
        scanner = tools / "cargo-audit"
        if not scanner.exists():
            scanner.symlink_to(shutil.which(self.binary))
        output = self.root / "github-output"
        output.unlink(missing_ok=True)
        return subprocess.run(
            ["bash", "-e", "-c", step["run"]],
            cwd=workdir,
            env={
                **os.environ,
                "AUDIT_BASE_SHA": base,
                "GITHUB_EVENT_NAME": event,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary or self.root / "step-summary"),
                "GITHUB_WORKSPACE": str(project),
                "RUNNER_TEMP": str(self.root),
            },
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_generated_pr_audit_classifies_base_existing_new_and_unavailable(self):
        for runtime in ("rust", "tauri"):
            with self.subTest(runtime=runtime):
                project, workdir = self.generated_gate(runtime)
                missing = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=project, text=True
                ).strip()
                self.write_lock("1.0.1", workdir)
                self.helper.commit_repository(project, "clean base lock")
                clean = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=project, text=True
                ).strip()
                self.write_lock("1.0.0", workdir)
                self.helper.commit_repository(project, "vulnerable base lock")
                affected = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=project, text=True
                ).strip()
                for base, label in (
                    (affected, "ベースにも存在"),
                    (clean, "PRで新規"),
                    (missing, "未確認"),
                    ("0" * 40, "未確認"),
                ):
                    with self.subTest(base=base, label=label):
                        result = self.run_gate(project, workdir, base)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(
                            f"{label} / security-fixture 1.0.0 / RUSTSEC-2026-0001",
                            result.stdout,
                            result.stderr,
                        )
                        self.assertIn(
                            "警告 / security-fixture 1.0.0 / RUSTSEC-2026-0002",
                            result.stdout,
                        )
                        self.assertIn(
                            result.stdout.strip(),
                            (self.root / "step-summary").read_text(),
                        )
                        self.assertFalse((self.root / "github-output").exists())

    def test_generated_audit_preserves_denied_warnings_and_reports_scheduled_blockers(self):
        project, workdir = self.generated_gate("rust")
        self.write_lock("1.0.1", workdir)
        self.helper.commit_repository(project, "nonblocking base warning")
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip()
        result = self.run_gate(project, workdir, event="schedule")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = self.root / "github-output"
        self.assertFalse(output.exists())
        result = self.run_gate(project, workdir, event="schedule", summary=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        config = workdir / ".cargo/audit.toml"
        with config.open("a") as stream:
            stream.write('\n[output]\ndeny = ["unmaintained"]\nquiet = false\n')
        result = self.run_gate(project, workdir, base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "ベースにも存在 / security-fixture 1.0.1 / RUSTSEC-2026-0002",
            result.stdout,
            result.stderr,
        )
        result = self.run_gate(project, workdir, event="schedule")
        self.assertNotEqual(result.returncode, 0)
        initial = output.read_text()
        self.assertRegex(initial, r"^fingerprint=[0-9a-f]{64}\n$")
        result = self.run_gate(project, workdir, event="schedule")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_text(), initial)
        self.write_lock("1.0.0", workdir)
        result = self.run_gate(project, workdir, event="schedule")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(output.read_text(), initial)

    def test_generated_preflight_and_scan_errors_fail_without_scheduled_alert(self):
        project, workdir = self.generated_gate("rust")
        result = self.run_gate(project, workdir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未確認", result.stdout)
        self.assertIn("committed Cargo.lock", result.stderr)
        lock = self.write_lock("1.0.0", workdir)
        result = self.run_gate(project, workdir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("committed Cargo.lock", result.stderr)
        self.helper.commit_repository(project, "track lock")
        inconsistent = '''version = 3

[[package]]
name = "dependency-fixture"
version = "1.0.0"
dependencies = ["other-fixture 2.0.0"]

[[package]]
name = "other-fixture"
version = "1.0.0"
'''
        for content in ("", "[[package]\nmalformed\n", inconsistent):
            with self.subTest(content=content):
                lock.write_text(content)
                result = self.run_gate(project, workdir, event="schedule")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "github-output").exists())
        self.helper.commit_repository(project, "inconsistent base lock")
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip()
        self.write_lock("1.0.0", workdir)
        result = self.run_gate(project, workdir, base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "未確認 / security-fixture 1.0.0 / RUSTSEC-2026-0001",
            result.stdout,
            result.stderr,
        )

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
