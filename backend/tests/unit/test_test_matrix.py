"""Keeps tests/TEST_MATRIX.md honest: every test it names must exist."""

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
MATRIX = TESTS_DIR / "TEST_MATRIX.md"

# `integration/test_x.py::test_name` or `unit/test_x.py::test_name` in backticks.
REFERENCE = re.compile(r"`((?:unit|integration)/test_\w+\.py)::(test_\w+)`")
# Section headings of the spec's list of required tests, as numbered in the matrix.
SPEC_ITEM = re.compile(r"^### (\d+)\. ", re.MULTILINE)


def test_every_referenced_test_exists() -> None:
    references = REFERENCE.findall(MATRIX.read_text(encoding="utf-8"))
    assert len(references) > 100  # the pattern still matches the file's format

    missing = []
    for file_name, test_name in references:
        path = TESTS_DIR / file_name
        source = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not re.search(rf"^(?:async )?def {test_name}\(", source, re.MULTILINE):
            missing.append(f"{file_name}::{test_name}")
    assert missing == []


def test_every_spec_item_has_a_section() -> None:
    numbers = [int(number) for number in SPEC_ITEM.findall(MATRIX.read_text(encoding="utf-8"))]
    assert numbers == list(range(1, 12))
