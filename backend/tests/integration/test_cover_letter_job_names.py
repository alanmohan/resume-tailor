"""A cover letter may name the job it applies for, even when the job's title
or company contains a figure ("Platform Engineer 2" at "Studio 54").

Connective cover-letter text cites no evidence, so a figure in it normally
makes it an unsupported claim that generation removes. The job's own name is
the exception: it comes from the posting, not from the candidate.
"""

import httpx

from tests.helpers_flow import generated
from tests.integration.test_profile_flow import confirmed_profile, source

RESUME = (
    "Riley Park\nriley.park@example.com\n\n"
    "Experience\n"
    "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
    "- Built Python pipelines for 12 analysts\n"
    "- Wrote SQL reports for the finance team\n\n"
    "Skills\nLanguages: Python, SQL\n"
)
JOB = {
    "title": "Platform Engineer 2",
    "company": "Studio 54",
    "description": "Requirements\n- Python experience\n- Working knowledge of SQL\n",
}


async def test_greeting_names_a_job_whose_title_and_company_contain_figures(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await confirmed_profile(client, auth_headers, [source("Resume", "resume", RESUME)])
    job = (await client.post("/api/jobs", json=JOB, headers=auth_headers)).json()

    draft = await generated(client, auth_headers, job["job_id"], "numbered-job-0001")

    greeting = draft["cover_letter"]["paragraphs"][0]
    assert greeting["text"] == (
        "Dear Hiring Manager, I am writing to apply for the Platform Engineer 2 role at Studio 54."
    )
    assert greeting["validation_status"] == "not_applicable"
    assert greeting["warnings"] == []
    assert all("Dear Hiring Manager" not in omitted["text"] for omitted in draft["omitted_claims"])
