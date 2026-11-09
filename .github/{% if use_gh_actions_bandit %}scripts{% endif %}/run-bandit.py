"""Run Bandit without treating skipped files or internal errors as a clean scan."""

import json
import os
import re
import subprocess  # nosec B404: fixed scanner argv, without a shell.
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ("src", ".github/scripts")


def _raise_walk_error(error: OSError) -> None:
    raise error


def main() -> int:
    for target in TARGETS:
        if not (ROOT / target).is_dir():
            print(f"Bandit target directory is missing: {target}", file=sys.stderr)
            return 1
    try:
        # Bandit's os.walk silently skips unreadable directories without this check.
        for target in TARGETS:
            for _ in os.walk(ROOT / target, onerror=_raise_walk_error):
                pass
    except OSError as exc:
        print(f"Could not traverse Bandit targets: {exc}", file=sys.stderr)
        return 1
    config = ROOT / ".bandit.yml"
    if not config.is_file():
        print("Bandit configuration is missing: .bandit.yml", file=sys.stderr)
        return 1
    command = [
        sys.executable,
        "-I",
        "-m",
        "bandit",
        # Bandit prints progress before JSON above 50 files unless quiet.
        "--quiet",
        "--recursive",
        *TARGETS,
        # The default '.git' substring exclusion also hides '.github' in a new repo.
        # Targets are already narrow; use only the reviewed config exclusions.
        "--exclude",
        "",
        "--configfile",
        str(config),
        "--format",
        "json",
        "--severity-level",
        "all",
        "--confidence-level",
        "all",
    ]
    try:
        # Source files are parsed by Bandit, never imported or executed here.
        result = subprocess.run(  # nosec B603: fixed executable/options, no shell.
            command, cwd=ROOT, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        print(f"Could not start Bandit: {exc}", file=sys.stderr)
        return 1
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    try:
        report = json.loads(result.stdout)
        if not isinstance(report, dict):
            raise TypeError("report is not an object")
        errors = report["errors"]
        findings = report["results"]
        metrics = report["metrics"]
        if not isinstance(errors, list) or not isinstance(findings, list):
            raise TypeError("errors/results are not lists")
        if not isinstance(metrics, dict):
            raise TypeError("metrics is not an object")
        scanned = [name for name in metrics if name != "_totals"]
        if not scanned:
            raise ValueError("no Python files were scanned")
        for finding in findings:
            print(
                f"{finding['filename']}:{finding['line_number']}: "
                f"{finding['test_id']} {finding['issue_severity']}/"
                f"{finding['issue_confidence']}: {finding['issue_text']}"
            )
        for error in errors:
            print(f"Bandit scan error: {error}", file=sys.stderr)
    except (ValueError, KeyError, TypeError) as exc:
        print(f"Invalid or incomplete Bandit report: {exc}", file=sys.stderr)
        return 1
    if (
        result.returncode
        or errors
        or findings
        or re.search(r"\]\s+ERROR\b", result.stderr)
    ):
        print("Bandit did not complete a clean scan.", file=sys.stderr)
        return 1
    print(f"Bandit passed: {len(scanned)} Python files scanned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
