import sys
from collections.abc import Generator

import pytest


class FileCollectionTracker:
    def __init__(self) -> None:
        self.has_test_files = False

    @pytest.hookimpl(wrapper=True)
    def pytest_collect_file(
        self,
    ) -> Generator[None, list[pytest.Collector], list[pytest.Collector]]:
        collectors = yield
        if collectors:
            self.has_test_files = True
        return collectors


def main() -> int:
    tracker = FileCollectionTracker()
    exit_code = pytest.main(sys.argv[1:], plugins=[tracker])
    if exit_code == pytest.ExitCode.NO_TESTS_COLLECTED and not tracker.has_test_files:
        print("No test files collected; skipping test execution.")
        return 0
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
