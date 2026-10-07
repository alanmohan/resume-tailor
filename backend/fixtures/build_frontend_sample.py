"""Regenerate frontend/src/sample/sampleData.ts from the fictional fixtures.

The "Try sample profile" button in the frontend must show exactly the same
fictional texts that the backend tests use. Generating the TypeScript file from
the fixture files keeps the two copies identical.

Usage (from anywhere):
    python backend/fixtures/build_frontend_sample.py
"""

import json
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURES_DIR.parent.parent
OUTPUT_PATH = REPO_ROOT / "frontend" / "src" / "sample" / "sampleData.ts"

# (label shown in the UI, source_type sent to the API, fixture file name)
SAMPLE_SOURCES = [
    ("Resume", "resume", "sample_resume.txt"),
    ("LinkedIn profile", "linkedin", "sample_linkedin.txt"),
    ("Background notes", "notes", "sample_notes.txt"),
]
SAMPLE_JOB_FILE = FIXTURES_DIR / "jobs" / "synthetic" / "close_fit.json"

HEADER = """\
/**
 * Fictional sample data for the "Try sample profile" option.
 *
 * Everything in this file is invented. The candidate, the employers, the
 * university and the job posting do not exist, and no real person's data is
 * included.
 *
 * Generated from backend/fixtures by backend/fixtures/build_frontend_sample.py.
 * Do not edit by hand: change the fixture files and run that script again.
 */
"""


def as_template_literal(text: str) -> str:
    """Wrap text in backticks so the TypeScript file stays readable.

    A template literal keeps real line breaks, but a backtick, a backslash or
    "${" inside it would change the string's value, so those are refused.
    """
    for forbidden in ("`", "\\", "${"):
        if forbidden in text:
            raise ValueError(f"sample text contains {forbidden!r}, which is unsafe in a template literal")
    return f"`{text}`"


def render_sources() -> str:
    entries = []
    for label, source_type, file_name in SAMPLE_SOURCES:
        text = (FIXTURES_DIR / "profiles" / file_name).read_text(encoding="utf-8")
        entries.append(
            "  {\n"
            f"    label: {json.dumps(label)},\n"
            f"    source_type: {json.dumps(source_type)},\n"
            f"    text: {as_template_literal(text)},\n"
            "  },\n"
        )
    return (
        'export const SAMPLE_SOURCES: { label: string; source_type: "resume" | "linkedin" | "notes"; text: string }[] = [\n'
        + "".join(entries)
        + "];\n"
    )


def render_job() -> str:
    job = json.loads(SAMPLE_JOB_FILE.read_text(encoding="utf-8"))
    return (
        "export const SAMPLE_JOB: { title: string; company: string; description: string } = {\n"
        f"  title: {json.dumps(job['title'])},\n"
        f"  company: {json.dumps(job['company'])},\n"
        f"  description: {as_template_literal(job['description'])},\n"
        "};\n"
    )


def render_file() -> str:
    return HEADER + "\n" + render_sources() + "\n" + render_job()


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_file(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
