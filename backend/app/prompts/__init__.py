"""Prompt templates.

Each prompt is a Markdown file in this folder, kept under version control so
changes to model instructions are reviewable like code. Templates contain
trusted instructions only: user-supplied text (resumes, notes, job postings)
is never interpolated into them. It is sent separately as delimited JSON data.
"""

import re
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent

# Recorded with generations and logs so an output can be traced to the prompt
# set that produced it. Bump it whenever a template changes.
PROMPT_VERSION = "2026-10-07.1"

_NAME_PATTERN = re.compile(r"[a-z0-9_]+")


@cache
def load_prompt(name: str) -> str:
    """Return the text of ``<name>.md`` from this folder (cached after first read).

    ``name`` is restricted to lower-case letters, digits and underscores, so
    it can never be used to read a file outside the prompts folder.
    """
    if not _NAME_PATTERN.fullmatch(name):
        raise ValueError(f"invalid prompt name: {name!r}")
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt template not found: {path.name}")
    return path.read_text(encoding="utf-8").strip()
