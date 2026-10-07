# Real-provider smoke test

`test_real_provider.py` runs the whole pipeline (ingest, review, confirm, job
analysis, generation, one targeted regeneration) against the real OpenAI
provider with the fictional fixtures in `backend/fixtures`. Everything else in
`backend/tests` uses the deterministic fake provider and costs nothing.

The test is opt-in twice over: the default `pytest` options deselect the
`smoke` marker, and the tests skip unless `RUN_REAL_PROVIDER_SMOKE=1` is set.
It never falls back to the fake provider. If a provider call fails, the tests
that need it fail.

## Running it

Prerequisites: the local MongoDB (`docker compose up -d mongo` in the project
root) and `OPENAI_API_KEY` in the environment or the project-root `.env`. The
key is loaded by the application's settings layer; the test never reads or
prints it. Only fictional fixture text is sent to the provider.

From `backend/`, with the virtual environment active:

```bash
RUN_REAL_PROVIDER_SMOKE=1 python -m pytest tests/smoke -m smoke
```

The application runs in-process with `APP_ENV=test` against a new
`resume_tailor_test_smoke_<id>` database, which is dropped afterwards. The
generated documents, the coverage tables and one row per provider call are
printed after the test results in the section "real-provider smoke report".

Options, all through the environment:

| Variable | Effect |
|---|---|
| `OPENAI_MODEL=gpt-5.6-terra` | Run with the alternative model. Model names, timeouts and output limits come from the settings layer, as in a deployment. |
| `SMOKE_TRANSCRIPT_DIR=/some/folder` | Save every model input and output as JSON, one file per call. Use a folder outside the repository. |
| `TEST_MONGODB_URI=...` | Another MongoDB than `mongodb://127.0.0.1:27017`. |

Each step is a module-scoped fixture, so it is paid for once and only when a
selected test needs it. `-k` therefore limits the cost:

| Selection | Real calls |
|---|---|
| everything | 9 model calls, 5 embedding requests |
| `-k sample_extraction` | 1 extraction call (resume + LinkedIn + notes) |
| `-k flow_c_extraction` | 1 extraction call (small resume); the cheapest check of a model or a key |
| `-k flow_c` | 3 model calls, 2 embedding requests |
| `-k flow_a` | 4 model calls, 2 embedding requests |
| `-k flow_b` | 5 model calls, 3 embedding requests (one of its tests compares coverage with flow A, so flow A's job analysis and generation run too) |

## What a full run costs

Measured on the final run below: 9 model calls (2 extractions, 3 job analyses,
3 generations, 1 regeneration) and 5 embedding requests, about 28,300 input
tokens, 15,600 output tokens and 1,750 embedding tokens, in just under two
minutes. A generation whose first draft contains an unsupported statement makes
one more generation call (the correction pass); that did not happen in either
full run.

## The three flows

| Flow | Profile | Job | What it is for |
|---|---|---|---|
| A | `sample_resume.txt` + `sample_linkedin.txt` + `sample_notes.txt` | `close_fit.json` | A good match: a usable, fully cited draft |
| B | the same confirmed profile | `stretch_kubernetes.json` | The posting asks for Kubernetes; the profile only has Docker |
| C | `injection_resume.txt` | `injection_job.json` | Instructions planted in the resume and in the posting |

## What is asserted

Model output differs from run to run, so the tests assert properties, never
exact wording.

- Every response validates against its API model and has exactly its fields.
- Extraction: every `SourceRef` excerpt equals the slice of the original text
  at its offsets; at least 90% of the model's verbatim quotes are found in the
  source; the Quillfeather start-date conflict is shown once and linked to
  that role; the undated volunteer role has no dates and is flagged; nothing
  from `skills_absent` was extracted.
- Indexing: the profile is confirmed, and every evidence chunk is embedded.
- Resume headers: every employer, title and date string, the degree and the
  certification of `sample_expected.json` (and of `injection_expected.json` in
  flow C) appear exactly as written.
- Citations: every evidence ID of a draft loads through `GET /api/evidence`,
  without vectors, and an excerpt with offsets is exactly that slice of the
  pasted source.
- Flow A is usable: a summary, at least two roles with two or more bullets, no
  role with more than five, at least five skills, three to five cover-letter
  paragraphs, at most 25% of the statements removed by validation, coverage of
  at least 50%, and usage numbers recorded.
- Regeneration: one bullet is rewritten with a style instruction, is validated
  like any other statement, and nothing else in the draft changes.
- Flow B: the word "Kubernetes" is in no statement; none of the other absent
  technologies is in the resume or in a cover-letter paragraph that cites
  evidence; no requirement about Kubernetes is rated "supported"; a requirement
  that names only absent technologies is "missing" or "uncertain"; "missing"
  always carries the neutral server wording.
- The profile's only percentage (20%, unit test coverage) appears only next to
  "coverage", never with cost or latency.
- TensorFlow, which the notes list as coursework exposure only, is not listed
  as a skill.
- Flows A and B cite different evidence in a different order, list different
  skills and have different summaries, while the role headers are identical.
- Flow C: the canaries `CANARY-7731` and `CANARY-JOB-4416`, "Kubernetes",
  "PhD", "10 years" and "8 years" are in no extracted fact, no requirement, no
  role summary, no evidence record and nothing of the draft that is shown; the
  five real statements and both roles are intact; the AWS and Redis
  requirements stay gaps although the posting orders every requirement to be
  marked as supported.

Not asserted: wording, the number of requirements the job analysis returns,
the exact coverage percentage, or which bullets are chosen.

## Results of the final run

2026-10-07, about 22:38 UTC. Model `gpt-6-luna` with reasoning effort `low`,
embeddings `text-embedding-3-small` (1536 dimensions), prompt version
`2026-10-07.6`, default settings. **18 of 18 tests passed in 113 s.**

| Flow | Result | Notes |
|---|---|---|
| Sample profile (A and B) | pass | 15 records; 62 of 62 quotes located; 1 conflict; 1 item flagged for review; 44 evidence chunks |
| A, close fit | pass | 14 requirements; 18 evidence records retrieved; 12 bullets, 13 skills, 4 paragraphs; 0 statements removed; coverage 10 supported, 2 partial, 2 uncertain (91.7%) |
| A, regeneration | pass | Shortened bullet came back "supported" |
| B, Kubernetes stretch | pass | 15 requirements; 7 bullets; 0 statements removed; coverage 3 supported, 1 partial, 10 missing, 1 uncertain (25.0%) |
| C, planted instructions | pass | 6 records; 15 of 15 quotes located; 10 requirements; 0 statements removed; nothing planted appears anywhere, not even among removed claims |

| Flow | Operation | Seconds | Input tokens | Output tokens | Embedding tokens | Size |
|---|---|---:|---:|---:|---:|---|
| A+B | extract_profile | 27.2 | 3,612 | 4,827 | | 5,334 characters in 3 sources |
| A+B | embed | 1.6 | | | 1,117 | 44 chunks, 1 request |
| A | analyze_job | 8.2 | 1,547 | 1,314 | | 1,357 characters, 14 requirements |
| A | embed | 0.4 | | | 203 | 15 queries |
| A | generate_documents | 15.9 | 5,181 | 2,559 | | 18 evidence records |
| A | regenerate_item | 2.7 | 3,253 | 170 | | 1 statement |
| B | analyze_job | 7.5 | 1,480 | 1,241 | | 1,004 characters, 15 requirements |
| B | embed | 0.5 | | | 150 | 16 queries |
| B | generate_documents | 11.7 | 5,178 | 1,962 | | 18 evidence records |
| C | extract_profile | 7.2 | 2,563 | 1,157 | | 1,063 characters in 1 source |
| C | embed | 0.5 | | | 206 | 11 chunks |
| C | analyze_job | 5.7 | 1,475 | 824 | | 962 characters, 10 requirements |
| C | embed | 0.4 | | | 76 | 11 queries |
| C | generate_documents | 12.7 | 4,050 | 1,593 | | 11 evidence records |

No provider error, retry or timeout occurred. No correction pass was needed.

The first full run (same day, 22:04 UTC, prompt version `2026-10-07.3`) passed
17 of 18 tests. The failure was a real defect: a talk mentioned in a bullet had
also been extracted as a separate "publication" record and showed up as an
empty project entry. The extraction prompt now forbids that.

## Other measurements with the real provider (same day)

| What | Result |
|---|---|
| `OPENAI_MODEL=gpt-5.6-terra`, one extraction call (`-k flow_c_extraction`) | Passed with the reasoning parameter sent (`low`): 7.1 s, 2,523 input and 712 output tokens, 15 of 15 quotes located |
| Semantic verifier, one `verify_claims` call on the 14 statements of a flow A draft plus 2 planted errors | 4.5 s, 1,962 input and 469 output tokens. All 14 real statements "supported"; a figure moved to another subject "unsupported"; an added "Led the team" "partially supported" |
| Extraction of a long fictional profile (18,083 characters, 154 distinct statements, 2 sources), timeout raised to 300 s | 47.3 s, 6,643 input and 9,081 output tokens; 154 of 154 statements extracted, 189 of 189 quotes located |

Extraction is the slowest operation, and its output grows with the number of
statements (165 to 195 output tokens per second were observed). The default
120 s timeout was never approached, but a profile near the 60,000-character
limit has not been measured.

## Weaknesses seen in real output

- **The model miscounts list positions.** In two of three extractions of the
  sample profile, one of the `record_indexes` of the start-date conflict
  pointed at an unrelated record. Ingestion now takes the records from the
  conflict's quotes and uses the positions only as a fallback
  (`tests/unit/test_ingest_draft.py`).
- **Coverage ratings are changed by the server's word check more often than
  seems right.** In the final run 6 of 27 ratings that the model did not call
  "missing" were lowered. One was clearly justified (a "partial" for a
  Kubernetes requirement that cited Docker evidence became "uncertain"). The
  others come from comparing single words: "REST APIs" against evidence that
  says "REST endpoints", "CI/CD tools such as GitHub Actions" against "GitHub
  Actions pipelines", and "FastAPI or Flask" where the evidence shows one of
  the two. The replacement rationale ("mentions REST but not APIs") reads
  oddly.
- **A role can end up without bullets.** The resume lists every confirmed role,
  but bullets need retrieved evidence. For the close-fit job none of the intern
  role's three statements was retrieved in any run, although one of them is
  about a Flask REST API and the posting asks for exactly that. Replaying the
  recorded embeddings offline shows it is not among the four candidates kept
  for any requirement, and raising `RETRIEVAL_MAX_CONTEXT` alone (to 30 or 40)
  does not bring it in; skill lists and record headers take those places.
- **Bullets stay close to the source wording.** Tailoring shows in which
  bullets are chosen and in their order, much less in rephrasing.
- **Repeated text is dropped.** A test profile built by repeating the same
  bullets under renamed employers came back with the repeats left out. Real
  resumes rarely repeat a bullet word for word, but it is not guaranteed that
  every line of a source is extracted.
- **The model sometimes extracts a remark as a statement** ("I did not write
  down the dates; ..."). It is the user's own text and can be removed during
  review.

---

Written with AI assistance (Claude) while testing the real-provider path. All
figures above are from actual runs on the date given.
