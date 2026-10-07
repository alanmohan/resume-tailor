"""Pasted text is data. The API stores and returns it character for character
as JSON strings, never as markup, and tells the browser not to reinterpret or
cache what it receives.

The frontend is what finally renders the text (as text nodes, not HTML); these
tests cover the server's half: nothing is turned into HTML on the way, and
every response carries the headers that keep a browser from guessing.
"""

import httpx

from tests.helpers_flow import cited_evidence_ids, generated
from tests.helpers_generation import all_text
from tests.integration.test_profile_flow import confirmed_profile, source

SCRIPT = '<script>alert("x")</script>'
IMAGE = "<img src=x onerror=alert(1)>"
RESUME_WITH_MARKUP = (
    "Riley Park\nriley.park@example.com\n\n"
    "Experience\n"
    "Data Engineer - Northwind Labs (Jan 2020 - Mar 2021)\n"
    f"- Built <b>Python</b> pipelines {SCRIPT} for 12 analysts\n"
    f"- Wrote an {IMAGE} SQL report\n\n"
    "Skills\nLanguages: Python, SQL\n"
)
JOB_WITH_MARKUP = f"Requirements\n- Python {SCRIPT} experience\n- Working knowledge of SQL\n"


def assert_json_that_is_not_sniffed_or_stored(response: httpx.Response) -> None:
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_api_responses_are_json_that_must_not_be_sniffed_or_cached(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post("/api/sessions")
    headers = {"Authorization": f"Bearer {created.json()['token']}"}

    responses = [
        created,  # carries the token: must never sit in a cache
        await client.get("/api/session", headers=headers),
        await client.get("/api/profile", headers=headers),  # 404 envelope
        await client.get("/api/session"),  # 401 envelope
        await client.get("/api/no-such-route"),  # router 404
        await client.post("/api/profiles/ingest", json={"sources": []}, headers=headers),  # 422
    ]

    assert [response.status_code for response in responses] == [201, 200, 404, 401, 404, 422]
    for response in responses:
        assert_json_that_is_not_sniffed_or_stored(response)
    # Pages outside /api are not personal data, but are not to be sniffed either.
    for path in ("/healthz", "/readyz", "/docs"):
        assert (await client.get(path)).headers["x-content-type-options"] == "nosniff"


async def test_markup_in_pasted_text_stays_plain_text_from_ingestion_to_the_draft(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    headers = auth_headers

    profile = await confirmed_profile(
        client, headers, [source("Resume", "resume", RESUME_WITH_MARKUP)]
    )
    job_response = await client.post(
        "/api/jobs", json={"description": JOB_WITH_MARKUP}, headers=headers
    )
    job = job_response.json()
    draft = await generated(client, headers, job["job_id"], "markup-draft-0001")

    # Stored and returned exactly as pasted: not escaped, not stripped, not run.
    bullets = [bullet["text"] for bullet in profile["records"][0]["bullets"]]
    assert bullets == [
        f"Built <b>Python</b> pipelines {SCRIPT} for 12 analysts",
        f"Wrote an {IMAGE} SQL report",
    ]
    first_ref = profile["records"][0]["bullets"][0]["source_ref"]
    assert RESUME_WITH_MARKUP[first_ref["start"] : first_ref["end"]] == first_ref["excerpt"]
    assert job["description"] == JOB_WITH_MARKUP
    assert job["requirements"][0]["text"] == f"Python {SCRIPT} experience"
    assert SCRIPT in all_text(draft)  # the fake provider quotes the evidence

    excerpts = []
    for evidence_id in cited_evidence_ids(draft):
        response = await client.get(f"/api/evidence/{evidence_id}", headers=headers)
        assert_json_that_is_not_sniffed_or_stored(response)
        excerpts.append(response.json()["excerpt"])
    assert f"Built <b>Python</b> pipelines {SCRIPT} for 12 analysts" in excerpts

    # It always travels as a JSON string; no response is ever an HTML document.
    assert_json_that_is_not_sniffed_or_stored(job_response)
    read_back = await client.get(f"/api/generations/{draft['generation_id']}", headers=headers)
    assert_json_that_is_not_sniffed_or_stored(read_back)
    assert SCRIPT.replace('"', '\\"') in read_back.text  # inside a JSON string literal
