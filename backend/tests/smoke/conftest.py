"""Prints the smoke report after the test results.

A terminal-summary section is shown even when pytest captures output, so the
generated documents and the measurements are visible without ``-s``.
"""

import pytest

from tests.smoke import report


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    if not report.LINES:
        return
    terminalreporter.section("real-provider smoke report")
    for line in report.LINES:
        terminalreporter.write_line(line)
