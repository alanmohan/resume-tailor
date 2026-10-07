"""The human-readable report of a smoke run.

The tests add lines to ``LINES`` while they run; ``conftest.py`` prints them
after the test results. Everything printed comes from the fictional fixtures.
"""

from typing import Any

from tests.smoke.metering import ProviderCall

LINES: list[str] = []


def add(*lines: str) -> None:
    LINES.extend(lines)


def _claim_line(claim: dict[str, Any], indent: str) -> str:
    notes = f"  !! {' '.join(claim['warnings'])}" if claim["warnings"] else ""
    return f"{indent}[{claim['validation_status']}] {claim['text']}{notes}"


def _entry_lines(entries: list[dict[str, Any]]) -> list[str]:
    lines = []
    for entry in entries:
        header = [entry["heading"], entry["subheading"], entry["date_range"], entry["location"]]
        lines.append("  " + " | ".join(part for part in header if part))
        lines += [_claim_line(bullet, "    - ") for bullet in entry["bullets"]]
    return lines


def add_draft(title: str, draft: dict[str, Any]) -> None:
    """Add one generated resume, cover letter and coverage table as plain text."""
    resume = draft["resume"]
    counts = draft["coverage_summary"]
    validation = draft["validation"]
    add(
        "",
        f"===== {title} =====",
        f"model {draft['model']}; retrieved {len(draft['retrieved_evidence_ids'])} evidence "
        f"records; usage {draft['usage']}",
        f"validation: needs_review {validation['needs_review_count']}, "
        f"unsupported {validation['unsupported_count']}; omitted {len(draft['omitted_claims'])}",
        "SUMMARY",
        *(_claim_line(claim, "  ") for claim in resume["summary"]),
        "EXPERIENCE",
        *_entry_lines(resume["experience"]),
        "PROJECTS",
        *_entry_lines(resume["projects"]),
        "EDUCATION",
        *_entry_lines(resume["education"]),
        "CERTIFICATIONS",
        *_entry_lines(resume["certifications"]),
        "SKILLS: " + ", ".join(skill["text"] for skill in resume["skills"]),
        "COVER LETTER",
        *(_claim_line(claim, "  ") for claim in draft["cover_letter"]["paragraphs"]),
        f"COVERAGE: supported {counts['supported']}, partial {counts['partial']}, "
        f"missing {counts['missing']}, uncertain {counts['uncertain']}, "
        f"percent {counts['percent']}",
        *(
            f"  [{item['status']}] ({item['importance']}) {item['requirement_text']} "
            f"-- {item['rationale']}"
            for item in draft["coverage"]
        ),
        "OMITTED CLAIMS",
        *(
            f"  - {item['section']}: {item['text']} -- {item['reason']}"
            for item in draft["omitted_claims"]
        ),
        "WARNINGS",
        *(f"  - {warning}" for warning in draft["warnings"]),
    )


def add_calls(calls: list[ProviderCall], generation_model: str, embedding_model: str) -> None:
    """Add one row per provider call: flow, operation, latency and tokens."""
    add(
        "",
        f"===== Provider calls ({generation_model}, {embedding_model}) =====",
        f"{'flow':<5}{'operation':<20}{'seconds':>9}{'input':>9}{'output':>9}{'embedding':>11}",
    )
    for call in calls:
        usage = call.usage
        add(
            f"{call.flow:<5}{call.operation:<20}{call.seconds:>9.1f}{usage.input_tokens:>9}"
            f"{usage.output_tokens:>9}{usage.embedding_tokens:>11}"
        )
    chat_calls = sum(1 for call in calls if call.operation != "embed")
    embedding_requests = sum(
        call.usage.provider_calls for call in calls if call.operation == "embed"
    )
    add(f"total: {chat_calls} model calls, {embedding_requests} embedding requests")
