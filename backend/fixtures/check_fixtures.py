"""Check that the fixtures are internally consistent.

Run this after editing any fixture:
    python backend/fixtures/check_fixtures.py

The script only reads files. It prints every problem it finds and exits with
status 1 if there is at least one, so a broken fixture is caught before a test
starts depending on it. README.md in this folder explains each rule.
"""

import json
import sys
from pathlib import Path

import build_frontend_sample

FIXTURES_DIR = Path(__file__).resolve().parent
PROFILES_DIR = FIXTURES_DIR / "profiles"
SYNTHETIC_DIR = FIXTURES_DIR / "jobs" / "synthetic"
LIVE_DIR = FIXTURES_DIR / "jobs" / "live"

SYNTHETIC_KEYS = {"synthetic", "title", "company", "description", "expected"}
EXPECTED_KEYS = {"required_keywords", "preferred_keywords", "expected_supported", "expected_missing"}
LIVE_KEYS = {
    "synthetic", "title", "company", "source_url", "date_retrieved", "fit_category",
    "why_selected", "selected_requirements", "access_notes",
}
FIT_CATEGORIES = {"close", "partial", "stretch"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def mentions(text: str, phrase: str) -> bool:
    """Case-insensitive substring test, the strictest reading of "is mentioned"."""
    return phrase.lower() in text.lower()


def load_sample_sources() -> dict[str, str]:
    """Return the three fictional sample texts keyed by resume/linkedin/notes."""
    expected = load_json(PROFILES_DIR / "sample_expected.json")
    return {
        name: (PROFILES_DIR / file_name).read_text(encoding="utf-8")
        for name, file_name in expected["source_files"].items()
    }


def check_all_json_parses() -> list[str]:
    problems = []
    for path in sorted(FIXTURES_DIR.rglob("*.json")):
        try:
            load_json(path)
        except ValueError as error:
            problems.append(f"{path.relative_to(FIXTURES_DIR)}: invalid JSON ({error})")
    return problems


def check_sample_profile() -> list[str]:
    """Every fact in sample_expected.json must be backed by the sample texts."""
    problems = []
    expected = load_json(PROFILES_DIR / "sample_expected.json")
    sources = load_sample_sources()
    resume = sources["resume"]
    all_text = "\n".join(sources.values())

    headers = [f"{r['title']} - {r['employer']} ({r['date_string']})" for r in expected["roles"]]
    headers += [f"{p['name']} - {p['context']} ({p['date_string']})" for p in expected["projects"]]
    headers += [f"{e['degree']} - {e['institution']} ({e['date_string']})" for e in expected["education"]]
    headers += [f"{c['name']} - {c['issuer']} ({c['date_string']})" for c in expected["certifications"]]
    for header in headers:
        if header not in resume.splitlines():
            problems.append(f"sample_resume.txt has no header line: {header}")

    for number in expected["key_numbers"]:
        if number["text"] not in sources[number["source"]]:
            problems.append(f"key number not found in {number['source']}: {number['text']}")

    contact = expected["contact"]
    if not contact["email"].endswith("@example.com") or "555-01" not in contact["phone"]:
        problems.append("sample contact details must use example.com and a 555-01xx phone number")
    for value in contact.values():
        if value not in resume:
            problems.append(f"contact value not found in sample_resume.txt: {value}")

    for skill in expected["skills_present"]:
        if not mentions(all_text, skill):
            problems.append(f"skills_present lists {skill}, which no sample source mentions")
    for skill in expected["skills_absent"]:
        if mentions(all_text, skill):
            problems.append(f"skills_absent lists {skill}, but a sample source mentions it")

    metric = expected["unrelated_metric"]
    if f"- {metric['bullet']}" not in resume.splitlines():
        problems.append("the unrelated 20% metric bullet is missing from sample_resume.txt")
    if all_text.count("%") != 1:
        problems.append("the sample profile must contain exactly one percentage (the unrelated 20%)")

    conflict = expected["expected_conflict"]
    for source_name, header in conflict["header_lines"].items():
        if header not in sources[source_name].splitlines():
            problems.append(f"conflict header line missing from {source_name}: {header}")
    if len(set(conflict["values"].values())) < 2:
        problems.append("expected_conflict must record two different start dates")

    duplicate = expected["duplicated_bullet"]
    for source_name in duplicate["sources"]:
        if sources[source_name].splitlines().count(f"- {duplicate['text']}") != 1:
            problems.append(f"the duplicated bullet must appear exactly once in {source_name}")

    for item in expected["ambiguous_items"]:
        if item["header_line"] not in sources[item["source"]].splitlines():
            problems.append(f"ambiguous header line missing from {item['source']}: {item['header_line']}")
    return problems


def check_injection_profile() -> list[str]:
    """Forbidden strings may only occur on the one injected line."""
    problems = []
    expected = load_json(PROFILES_DIR / "injection_expected.json")
    lines = (PROFILES_DIR / expected["source_file"]).read_text(encoding="utf-8").splitlines()
    injected = expected["injected_line"]

    if lines.count(injected) != 1:
        problems.append("injection_resume.txt must contain the injected line exactly once")
    if expected["canary"] not in injected:
        problems.append("the injected line must contain the canary")
    legitimate_text = "\n".join(line for line in lines if line != injected)
    for phrase in expected["must_not_claim"]:
        if not mentions(injected, phrase):
            problems.append(f"must_not_claim phrase is not in the injected line: {phrase}")
        if mentions(legitimate_text, phrase):
            problems.append(f"must_not_claim phrase also appears outside the injected line: {phrase}")
    return problems


def check_synthetic_jobs() -> list[str]:
    """Keywords must be in the description; support must match the sample profile."""
    problems = []
    profile_text = "\n".join(load_sample_sources().values())
    for path in sorted(SYNTHETIC_DIR.glob("*.json")):
        job = load_json(path)
        name = path.name
        missing_keys = (SYNTHETIC_KEYS - job.keys()) | (EXPECTED_KEYS - job.get("expected", {}).keys())
        if missing_keys:
            problems.append(f"{name}: missing keys {sorted(missing_keys)}")
            continue
        if job["synthetic"] is not True:
            problems.append(f"{name}: synthetic must be true")

        expected = job["expected"]
        keywords = expected["required_keywords"] + expected["preferred_keywords"]
        supported, missing = expected["expected_supported"], expected["expected_missing"]
        for keyword in keywords:
            if not mentions(job["description"], keyword):
                problems.append(f"{name}: keyword not in description: {keyword}")
        if sorted(supported + missing) != sorted(keywords):
            problems.append(f"{name}: expected_supported + expected_missing must equal all keywords exactly once")
        for keyword in supported:
            if not mentions(profile_text, keyword):
                problems.append(f"{name}: expected_supported keyword absent from the sample profile: {keyword}")
        for keyword in missing:
            if mentions(profile_text, keyword):
                problems.append(f"{name}: expected_missing keyword is present in the sample profile: {keyword}")

        canary = job.get("injection", {}).get("canary")
        if canary and canary not in job["description"]:
            problems.append(f"{name}: canary is not in the description")
    return problems


def check_live_jobs() -> list[str]:
    """Provenance must be complete; excerpts must match the local full text if present."""
    problems = []
    categories = set()
    for path in sorted(LIVE_DIR.glob("*.json")):
        job = load_json(path)
        name = path.name
        missing_keys = LIVE_KEYS - job.keys()
        if missing_keys:
            problems.append(f"{name}: missing keys {sorted(missing_keys)}")
            continue
        if job["synthetic"] is not False:
            problems.append(f"{name}: synthetic must be false")
        if job["fit_category"] not in FIT_CATEGORIES:
            problems.append(f"{name}: fit_category must be one of {sorted(FIT_CATEGORIES)}")
        if not job["source_url"].startswith("https://"):
            problems.append(f"{name}: source_url must be an https URL")
        if not job["selected_requirements"]:
            problems.append(f"{name}: selected_requirements is empty")
        categories.add(job["fit_category"])

        local_copy = LIVE_DIR / "_local_full" / f"{path.stem}.txt"
        if local_copy.exists():
            full_text = local_copy.read_text(encoding="utf-8")
            for item in job["selected_requirements"]:
                if item["text"] not in full_text:
                    problems.append(f"{name}: excerpt is not verbatim in the local full text: {item['text'][:60]}")
    if categories and categories != FIT_CATEGORIES:
        problems.append(f"live jobs must cover close, partial and stretch; found {sorted(categories)}")
    return problems


def check_frontend_sample() -> list[str]:
    """The generated TypeScript sample must match the fixtures it was built from."""
    output_path = build_frontend_sample.OUTPUT_PATH
    if not output_path.exists():
        return [f"{output_path.name} is missing; run build_frontend_sample.py"]
    if output_path.read_text(encoding="utf-8") != build_frontend_sample.render_file():
        return [f"{output_path.name} is out of date; run build_frontend_sample.py"]
    return []


def main() -> int:
    # The other checks load these files, so unreadable JSON is reported first.
    problems = check_all_json_parses()
    if not problems:
        content_checks = [
            check_sample_profile,
            check_injection_profile,
            check_synthetic_jobs,
            check_live_jobs,
            check_frontend_sample,
        ]
        problems = [problem for run_check in content_checks for problem in run_check()]
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print(f"fixture check finished: {len(problems)} problem(s) found")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
