import subprocess
import sys
from pathlib import Path


def main() -> int:
    test_directory = Path(__file__).resolve().parent
    if not any(
        path.is_file()
        for pattern in ("test_*.py", "*_test.py")
        for path in test_directory.rglob(pattern)
    ):
        print("No test files found in tests/; skipping pytest.")
        return 0

    result = subprocess.run(
        [sys.executable, "-m", "pytest", *sys.argv[1:]],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
