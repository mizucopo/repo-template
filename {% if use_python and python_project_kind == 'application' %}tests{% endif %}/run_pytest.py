import sys

import pytest


class CollectionTracker:
    def __init__(self) -> None:
        self.has_tests = False

    def pytest_collectstart(self, collector: pytest.Collector) -> None:
        if collector.path.is_file():
            self.has_tests = True

    def pytest_itemcollected(self) -> None:
        self.has_tests = True

    def pytest_deselected(self, items: list[pytest.Item]) -> None:
        if items:
            self.has_tests = True


def main() -> int:
    tracker = CollectionTracker()
    exit_code = pytest.main(sys.argv[1:], plugins=[tracker])
    if exit_code == pytest.ExitCode.NO_TESTS_COLLECTED and not tracker.has_tests:
        print("No test files collected; skipping test execution.")
        return 0
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
