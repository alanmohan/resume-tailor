> AI-generated documentation (Claude Code, 2026-10-07). Reviewed and owned by the repository author.

# Running and testing Resume Tailor locally

Every command, path and setting name here was checked against the repository on 2026-10-07. Where a command was run for this document, the result is in [section 11](#11-results-observed-while-writing-this-document) with the time it was observed. Where something could not be run or was still being changed, it is marked **not verified**.

Contents

1. [Prerequisites](#1-prerequisites)
2. [One-time setup](#2-one-time-setup)
3. [Running the app](#3-running-the-app)
4. [Automated checks](#4-automated-checks)
5. [How the tests isolate the database](#5-how-the-tests-isolate-the-database)
6. [The fake provider](#6-the-fake-provider)
7. [Opt-in real-provider smoke test](#7-opt-in-real-provider-smoke-test)
8. [Playwright end-to-end suite](#8-playwright-end-to-end-suite)
9. [Fixtures and their scripts](#9-fixtures-and-their-scripts)
10. [Experience master file](#10-experience-master-file)
11. [Results observed while writing this document](#11-results-observed-while-writing-this-document)
12. [Specification test matrix](#12-specification-test-matrix)
13. [Not verified](#13-not-verified)

## 1. Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 (`python3.12`) | The suite was run on 3.12.2. `render.yaml` pins the same version. |
| Node.js | 20.19 or newer | `frontend/package.json` declares `"node": ">=20.19"`. The commands here were run on Node 23.10.0 with npm 10.9.2; on that version npm prints `EBADENGINE` warnings for some development tools, and the commands still run. |
| Docker | any recent version with `docker compose` | Runs MongoDB 8.0 for development and tests. Optional only if you point the app at another MongoDB, see below. |

Ports used:

| Port | Used by |
|---|---|
| 27017 | MongoDB (bound to 127.0.0.1 only) |
| 8000 | API in development |
| 5173 | Frontend development server (fixed; the server refuses to start on another port) |
| 8010, 5183 | API and frontend started by the Playwright suite |

**Without Docker.** Use a dedicated development database on MongoDB Atlas with a restricted IP access list and set `MONGODB_URI` and `MONGODB_DATABASE` in `.env`. Never point the automated tests at a database that holds real data. The tests take their database address from `TEST_MONGODB_URI` (default `mongodb://127.0.0.1:27017`), not from `.env`.

## 2. One-time setup

From the repository root:

```bash
test -f .env || cp .env.example .env
docker compose up -d mongo
```

- The first line creates `.env` only if it does not exist. It never overwrites an existing file.
- Put your OpenAI key in `.env` as `OPENAI_API_KEY`. Do not print it, commit it or copy it anywhere else. `.env` is in `.gitignore`.
- The backend reads `<repository root>/.env` whatever directory it is started from. Real environment variables override the file.
- `docker compose up -d mongo` starts the `mongo` service from `compose.yaml`, reachable only from this machine, with data in a named volume.

Backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Frontend:

```bash
cd frontend
npm ci
cp .env.example .env.local
```

`frontend/.env.example` already contains `VITE_API_BASE_URL=http://127.0.0.1:8000`, which is right for local development. This is the only frontend setting; it is public and no secret may ever have a `VITE_` name.

If you need to add or remove a frontend package, the frontend's authors reported that npm 10.9.2 fails to resolve this dependency tree and that `npx npm@11 install <package>` works. `npm ci` works with npm 10.9.2.

## 3. Running the app

### API

In `backend/`, with the virtual environment active.

**Demo mode (no OpenAI calls, no cost).** Start here:

```bash
AI_PROVIDER=fake uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**Real provider.** `AI_PROVIDER` defaults to `openai`, so the command from the specification uses the key in `.env` and every extraction, analysis and generation is billed:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Check it:

```bash
curl -s http://127.0.0.1:8000/healthz     # {"status":"ok"}
curl -s http://127.0.0.1:8000/readyz      # "status":"ready" and "provider_mode":"fake" or "openai"
```

API documentation generated from the code is at <http://127.0.0.1:8000/docs>.

If `/readyz` answers 503, its body says which check failed: `"database":"unavailable"` (is the `mongo` container running?) or `"provider":"not_configured"` (no `OPENAI_API_KEY` while `AI_PROVIDER=openai`).

Invalid configuration stops startup with a message that names the setting, for example an unknown `OPENAI_MODEL` or a `RETRIEVAL_MODE` other than `python`.

### Frontend

In `frontend/`:

```bash
npm run dev -- --host 127.0.0.1
```

Open <http://127.0.0.1:5173>. The API allows exactly `http://127.0.0.1:5173` and `http://localhost:5173` by default (`CORS_ORIGINS`); any other origin is refused by the browser.

### Browser walk-through

1. Start: tick the acknowledgement, choose "Try sample profile" (fictional data), submit.
2. Profile review: one conflict (two start dates for the same role) must be resolved or dismissed; then Confirm. The page shows real embedding counts while indexing.
3. Target job: use the sample job or paste one; review the requirements; Generate.
4. Workspace: open evidence badges, edit a statement, Revalidate, regenerate one statement, correct a coverage status, Copy, Print / Save as PDF.
5. "Clear my data" in the header deletes everything.

In demo mode a "Demo mode" banner is shown on every screen and the generated text is assembled from your own evidence sentences rather than written by a model.

## 4. Automated checks

### Backend, from `backend/` with the virtual environment active

```bash
python -m pytest tests/unit -q
python -m pytest tests/integration -q
```

- `tests/unit`: pure functions; no database, no network.
- `tests/integration`: the real routes and middleware, called in-process, against the local `mongo` service with the fake provider. Needs `docker compose up -d mongo`.
- Neither calls OpenAI or reads `.env`.
- `pyproject.toml` sets `addopts = "-m 'not smoke'"`, so plain `python -m pytest` runs both folders and leaves the real-provider smoke tests out.

Lint and format check (configuration in `pyproject.toml`):

```bash
ruff check .
ruff format --check .
```

### Frontend, from `frontend/`

```bash
npm run lint
npm run typecheck
npm run test -- --run
npm run build
npx playwright install chromium
npm run test:e2e
```

| Command | What it does |
|---|---|
| `npm run lint` | ESLint over the TypeScript sources |
| `npm run typecheck` | `tsc -b --noEmit` over three TypeScript projects: the app (`tsconfig.app.json`), the Vite configuration (`tsconfig.node.json`) and the Playwright configuration with `e2e/` (`tsconfig.e2e.json`) |
| `npm run test -- --run` | Vitest once (without `--run` it watches). Runs `src/**/*.test.{ts,tsx}` in jsdom with `fetch` mocked; needs no server and no database. The per-test timeout is 20 seconds (`vite.config.ts`), because tests that render the whole app can be slow on a busy machine. |
| `npm run build` | Type-checks, then writes the production bundle to `dist/` |
| `npx playwright install chromium` | Downloads the browser Playwright drives; needed once |
| `npm run test:e2e` | The Playwright suite, see [section 8](#8-playwright-end-to-end-suite) |

## 5. How the tests isolate the database

Code: `backend/tests/conftest.py`, `backend/app/config.py`.

- **Settings never come from `.env`.** Test settings are built with `_env_file=None` and explicit values: `APP_ENV=test`, `AI_PROVIDER=fake`, no API key.
- **A unique database per run.** The name is `resume_tailor_test_<12 random hex characters>`, created for the pytest session and dropped when it ends. Collections are emptied after every test.
- **A safety guard on the name.** `assert_test_database` refuses any name that does not start with `resume_tailor_test`, before use and again before dropping. Independently, the application's own configuration refuses `APP_ENV=test` with a database name that lacks that prefix, so a mis-set variable cannot aim a test at the development or production database.
- **The database address** comes from `TEST_MONGODB_URI` (default `mongodb://127.0.0.1:27017`).
- **No server process.** Tests call the application through `httpx.ASGITransport`, in-process; the fixtures enter and leave the application's lifespan themselves.
- **Sessions are real.** `create_session` goes through `POST /api/sessions`; calling it twice gives two isolated visitors, which is how the isolation tests work.

The development database (`resume_tailor_dev` by default) is never touched by the tests.

## 6. The fake provider

Code: `backend/app/providers/fake/`.

`AI_PROVIDER=fake` replaces OpenAI with deterministic, rule-based code:

| Operation | What the fake does |
|---|---|
| Extraction | Reads common resume layouts line by line: section headings, `Title - Organisation (dates)` header lines, bullet lines, comma-separated skills |
| Job analysis | Reads headings and bullets of the posting |
| Generation | Reuses evidence text, at most shortened to whole sentences, and cites it |
| Embeddings | Hashed bag-of-words vectors, 256 dimensions, model name `fake-embedding-256` |

It exists for tests, the Playwright suite and demo mode. It keeps call counters (used to prove that a repeated `Idempotency-Key` makes no second call) and can be told to fail or to wait (used for the timeout, rate-limit and clear-data-in-flight tests).

Three rules keep it honest:

- it is used only when `AI_PROVIDER=fake` is set explicitly;
- configuration validation rejects it when `APP_ENV=production`;
- it is never a fallback: a failing real provider call is returned as an error.

Because the fake only reuses evidence text, it passes the grounding checks by construction. The tests named "... whatever the model writes" therefore replace document generation with a scripted, deliberately dishonest model output; those are the tests that show the server-side checks hold independently of the model.

## 7. Opt-in real-provider smoke test

Code: `backend/tests/smoke/` (`test_real_provider.py`, `metering.py`, `report.py`, `conftest.py`). 18 tests.

It runs the application in-process against a disposable `resume_tailor_test_smoke_<random>` database with the **real OpenAI provider**, using only the fictional fixtures. It costs money. Two things are needed to run it, so it cannot start by accident:

```bash
cd backend
RUN_REAL_PROVIDER_SMOKE=1 python -m pytest tests/smoke -m smoke
```

- Without `-m smoke` the tests are deselected (`addopts` in `pyproject.toml`).
- With `-m smoke` but without `RUN_REAL_PROVIDER_SMOKE=1` they are skipped.
- The API key is read by the settings layer from the environment or the root `.env`; the test code never reads it. `OPENAI_MODEL=gpt-5.6-terra` in the environment selects the other model.
- Optional: `SMOKE_TRANSCRIPT_DIR=<folder outside the repository>` saves every model input and output as JSON for reading afterwards.
- There is no fallback. If a provider call fails, the tests that needed it fail.

Three flows, each ingest, review, confirm, job analysis and generation:

| Flow | Profile | Job | What is asserted |
|---|---|---|---|
| A | sample resume + LinkedIn + notes | `close_fit` | Facts link to their sources; the conflict and the undated role are shown; resume headers equal the confirmed facts; every citation opens; one bullet can be regenerated |
| B | the same confirmed profile | `stretch_kubernetes` | Kubernetes is never claimed; coverage reports the gaps; the 20% figure stays with test coverage; coursework exposure is not listed as a skill; the two jobs get different emphasis |
| C | `injection_resume` | `injection_job` | The planted lines change nothing in extraction, job analysis or the documents; real facts survive and gaps stay gaps |

Model output varies from run to run, so the tests assert properties, never exact wording. A report with the generated documents, latency and token counts per call is printed after the test results.

**Whether this test has been run, and with what result, is not recorded in this document.** It was being written while this document was prepared and was not run for it. See [section 13](#13-not-verified).

## 8. Playwright end-to-end suite

Code: `frontend/playwright.config.ts`, `frontend/e2e/`.

```bash
cd frontend
npx playwright install chromium     # once
npm run test:e2e
```

Prerequisites: the `mongo` container is running and the backend virtual environment exists at `backend/.venv` (the configuration calls `backend/.venv/bin/python` directly). No OpenAI call is made.

Playwright starts its own isolated services and stops them afterwards:

| Service | Address | How |
|---|---|---|
| API | `127.0.0.1:8010` | `uvicorn` with `APP_ENV=test`, `AI_PROVIDER=fake`, database `resume_tailor_test_e2e`, `CORS_ORIGINS=http://127.0.0.1:5183`, and the session-creation and daily AI-call limits raised so the suite is not stopped half way |
| Frontend | `127.0.0.1:5183` | A production build made with `VITE_API_BASE_URL=http://127.0.0.1:8010`, written to `dist-e2e/` (so `dist/` is left alone) and served with `vite preview` |

- The ports differ from the development ports on purpose, and an already running server on them is **not** reused, so a run cannot touch a developer's own session or database. Stop anything else using 8010 or 5183 first.
- The database `resume_tailor_test_e2e` is dropped at the start of every run, not at the end. To remove it afterwards:

  ```bash
  backend/.venv/bin/python -c "from pymongo import MongoClient; MongoClient('mongodb://127.0.0.1:27017').drop_database('resume_tailor_test_e2e')"
  ```

- Browser: Chromium only. Each test fails if the page throws an uncaught error or logs an unexpected console error. `window.print` is replaced by a counter, because a real print dialog would block a headless browser. The print stylesheet is checked through print-media emulation, and the long-resume spec also has Chromium produce a PDF and checks that it has at least two pages and contains fonts (real text, not an image).
- Reports: `playwright-report/` (HTML) and `test-results/` (traces and screenshots of failures). Both are git-ignored.

Specs at the time of writing:

| Spec | What it covers |
|---|---|
| `critical-path.spec.ts` | The whole workflow with the sample profile: input, profile review, job analysis, generation, citations, editing, revalidation, print, clear data |
| `workspace-review.spec.ts` | A dishonest edit is flagged after revalidation and gates printing; regeneration, a coverage correction and Copy; a profile edit marks the draft out of date |
| `failures.spec.ts` | Provider timeout during extraction keeps the text and Retry succeeds; a rate-limited generation is retried with the same idempotency key; a session ending mid-flow is explained; a double click sends one request |
| `isolation.spec.ts` | A second browser session cannot see or reach the first one's data, in the app or through the API |
| `deep-links.spec.ts` | Every screen survives a refresh; unknown addresses and links without a session are handled |
| `escaping.spec.ts` | Script and image markup in pasted text is displayed as text on every screen and never executed |
| `keyboard.spec.ts` | Start, the clear-data dialog and an evidence item operated with the keyboard alone, with visible focus |
| `mobile.spec.ts` | The workflow at 375 x 812 with no sideways scrolling; workspace tabs and the evidence sheet |
| `print-long-document.spec.ts` | A resume with sixteen roles prints as one unclipped column over at least two pages |

The suite was still being written while this document was prepared; the list above is what existed then. Its pass or fail status is not recorded here. See [section 13](#13-not-verified).

## 9. Fixtures and their scripts

Described in full in `backend/fixtures/README.md`.

| Path | Content |
|---|---|
| `backend/fixtures/profiles/sample_*.txt`, `sample_expected.json` | A fictional candidate (resume, LinkedIn, notes) with deliberate traps: Docker but no Kubernetes; exactly one percentage (20% test coverage); one cross-source date conflict; one undated role; one duplicated bullet |
| `backend/fixtures/profiles/injection_resume.txt`, `injection_expected.json` | A second fictional candidate whose resume contains a planted instruction line and a canary string |
| `backend/fixtures/jobs/synthetic/*.json` | Six invented postings with known expected outcomes: `close_fit`, `partial_fit`, `stretch_kubernetes`, `metric_trap`, `injection_job`, `no_match` |
| `backend/fixtures/jobs/live/*.json` | Provenance and short excerpts of four real public postings retrieved on 2026-10-07 (close, partial, partial, stretch) |
| `backend/fixtures/jobs/live/_local_full/` | Full text of those postings. Git-ignored; local testing only; never commit or redistribute |
| `frontend/src/sample/sampleData.ts` | The sample texts and the `close_fit` job for "Try sample profile". Generated, not hand-written |

Scripts (standard library only; run from the repository root):

```bash
python backend/fixtures/check_fixtures.py            # consistency of every fixture; exit status 1 on any problem
python backend/fixtures/build_frontend_sample.py     # regenerate frontend/src/sample/sampleData.ts
python backend/fixtures/jobs/live/fetch_live_jobs.py # re-download _local_full/ after a fresh clone (network, no credentials)
```

Run `build_frontend_sample.py` and then `check_fixtures.py` after changing a sample text or `close_fit.json`.

## 10. Experience master file

The specification asks for a configurable `EXPERIENCE_MASTER_PATH`, a seed/test command that runs the real experience master file against retrieved job descriptions, and a short report of actual results.

**The command exists: `backend/scripts/evaluate_experience_master.py`.** It appeared while this document was being written and was still being changed, so read its module docstring for the current details. **No result of a real run is recorded in this document**; see the status at the end of this section.

Run it from `backend/`. It calls the real AI provider and is billed:

```bash
EXPERIENCE_MASTER_PATH="../Experience Master.docx" \
    .venv/bin/python -m scripts.evaluate_experience_master
```

What it does, according to the script:

1. Reads the master file (`.docx` through `python-docx`, or `.txt` / `.md`) and picks at most three live postings from `backend/fixtures/jobs/live/`: one close fit, one partial fit and one stretch role. Each posting needs its full text in `_local_full/` (download it with `fetch_live_jobs.py`, section 9).
2. Runs the application in-process with the real provider against a disposable `resume_tailor_test_master_<random>` database: create a session, ingest the master text as one source, resolve conflicts, confirm, then for each posting analyse the job, generate, and open every cited evidence record; finally delete the session. The database is dropped at the end.
3. Computes checks on what came back, prints everything to the terminal for a human reviewer, and writes a report file.

Budget stated in the script: one extraction, one confirmation, at most three job analyses and three generations; nothing is retried, and the fake provider is never used in place of a failed step.

| Variable | Meaning | Default |
|---|---|---|
| `EXPERIENCE_MASTER_PATH` | Path of the master file | `<repository root>/Experience Master.docx` |
| `MASTER_EVAL_JOBS` | Comma-separated slugs of one to three live postings to use instead of the default choice | one per fit category |
| `MASTER_EVAL_REPORT_PATH` | Where the report is written | `backend/scripts/out/experience_master_report.md` (the folder is git-ignored) |
| `MASTER_EVAL_TRANSCRIPT_DIR` | Folder for the raw run. It holds the master file's content, so the script refuses a folder inside the repository | not saved |
| `MASTER_EVAL_REPLAY` | Path of a saved `run.json`; checks and report are recomputed without calling anything | |
| `MASTER_EVAL_SELFTEST` | `1` checks the script itself with the fake provider and the fictional fixtures; the master file is not read and nothing is billed | |
| `TEST_MONGODB_URI` | MongoDB address | `mongodb://127.0.0.1:27017` |

**Privacy.** The terminal output of a real run shows the master file's content and the generated documents; do not paste it anywhere public. The report file is meant to hold only aggregate numbers, pass/fail flags and the public postings' details; the script checks the report against the master text before writing it and refuses to write a report that fails that check.

Exit status: 0 no failures, 1 failures were found, 2 nothing was run (for example the master file or the API key is missing), 3 the report was withheld by the privacy check.

**Status when this document was written**

- Self-test mode was run for this document at 18:33 ET: `MASTER_EVAL_SELFTEST=1 .venv/bin/python -m scripts.evaluate_experience_master` exited 0, reported 0 failures, deleted its session data and left no report file. That shows the script runs; it says nothing about the real profile.
- A real run (the actual master file, the real provider, the live postings) was **not** made for this document, and no report file existed in `backend/scripts/out/` at that time. If one has been made since, its report is the record; this document does not contain or summarise it.
- The script's docstring refers to `scripts/README.md`, which did not exist yet.

## 11. Results observed while writing this document

Machine: macOS, Python 3.12.2, Node 23.10.0, npm 10.9.2, MongoDB 8.0.32 in Docker. Date: 2026-10-07 (ET). **Other work on the code was in progress at the same time**, so these are snapshots. Run the commands again before relying on them.

| Command | Observed | Time (ET) |
|---|---|---|
| `python -m pip install -r requirements.txt -r requirements-dev.txt` into a new Python 3.12.2 virtual environment | installed; `pip check` found no broken requirements | 18:13 |
| `python -m pytest tests/unit -q` | 474 passed; later 499 passed (tests were added in between). 498 passed from the new virtual environment above. | 17:51, 18:21, 18:14 |
| `python -m pytest tests/integration -q` | 330 passed in 64 s | 17:52 |
| the same, second run | **9 failed, 321 passed in 272 s.** The machine's load average was about 100 on 8 cores, and `validation.py` and `drafting.py` were being edited by other work while the run was in progress. The two test files containing the three failures shown at the end of the output passed when run alone straight afterwards (22 passed). The other six failures were not identified. | 18:21, 18:27 |
| the same, third run | 331 passed in 183 s (one test more than in the first run; the machine was still heavily loaded) | 18:37 |
| `python -m pytest tests/smoke -m smoke` (without the opt-in variable) | 18 skipped | 18:10 |
| `ruff check .` | All checks passed | 18:12, 18:26 |
| `ruff format --check .` | all files already formatted (137 at the second run) | 18:12, 18:26 |
| `python backend/fixtures/check_fixtures.py` | 0 problems found | 18:14 |
| `MASTER_EVAL_SELFTEST=1 .venv/bin/python -m scripts.evaluate_experience_master` | exit status 0, 0 failures (fake provider, fictional fixtures) | 18:33 |
| API behaviour described in [API.md](API.md) | A throwaway script called every route in-process (fake provider, disposable `resume_tailor_test_docs_*` database) and the status codes, error codes and `details` fields matched the document | 18:17 |
| `npm run lint` | no findings | 17:52, 18:27 |
| `npm run typecheck` | failed at 17:52 and 18:12 on `e2e/` files that were being written; **clean at 18:27** after `tsconfig.e2e.json` was added | 18:27 |
| `npm run test -- --run` | **never clean in four attempts.** 20 and 25 of 242 tests failed with the earlier 5-second timeout; 29 failed with the 20-second timeout. Nearly every failure was a timeout, and the set of failing tests changed from run to run. Run alone with one worker and a 120-second timeout, the three most affected files gave 50 passed and 1 failed (an element was not found within the testing library's own wait). The load average was between 40 and 180 on 8 cores throughout, caused by other jobs. This points to an overloaded machine rather than wrong behaviour, but that is an inference: **a clean run was not observed.** | 17:53, 17:56, 18:27, 18:32 |
| `npm run build` | not run for this document | |
| `npm run test:e2e` | not run for this document | |
| `uvicorn ...` and `npm run dev` | not started for this document | |

## 12. Specification test matrix

The eleven items are the "Required meaningful tests" of the specification (section 9). The backend column summarises `backend/tests/TEST_MATRIX.md`, which lists every test by name and is itself checked by `tests/unit/test_test_matrix.py` (it fails if a listed test does not exist). Frontend unit tests are named by file under `frontend/src/`; end-to-end specs by file under `frontend/e2e/`.

| # | Specification item | Backend tests | Frontend unit tests | End-to-end specs |
|---|---|---|---|---|
| 1 | Ingest, edit and confirm profile, index, analyse job, generate, inspect evidence, edit and revalidate, print, clear data | `integration/test_full_flow.py`, `test_profile_flow.py`, `test_generation_create.py` | `pages/start`, `pages/profile`, `pages/job/__tests__`, `pages/workspace/__tests__` | `critical-path.spec.ts` |
| 2 | Job asks for Kubernetes, profile mentions Docker only | `integration/test_fixture_grounding.py`, `test_generation_grounding.py`, `test_generation_editing.py`; `unit/test_validation_claims.py`, `test_coverage_rules.py` | | `workspace-review.spec.ts` (a skill that is not in the profile, added by hand, is flagged) |
| 3 | Unrelated 20% metric is not reused | `integration/test_fixture_grounding.py`, `test_generation_grounding.py`; `unit/test_validation_claims.py`, `test_generation_compose.py`, `test_coverage_rules.py` | | |
| 4 | Hidden instructions cannot override grounding or reach other profiles | `integration/test_fixture_grounding.py`, `test_ingest_api.py`, `test_job_api.py`, `test_generation_grounding.py`; `unit/test_ingest_openai_ops.py`, `test_generation_openai_ops.py`, `test_ingest_instruction_guard.py` | | |
| 5 | Unknown evidence ID, cross-user ID, expired or revoked token, mixed profile versions, unindexed edits | `integration/test_evidence_api.py`, `test_sessions.py`, `test_profile_job_auth.py`, `test_generation_failures.py`, `test_generation_create.py`, `test_retrieval_scoping.py`, `test_index_confirm_api.py` | `lib/api.test.ts`, `lib/session.test.ts`, `components/app/AppShell.test.tsx` (401 ends the session) | `isolation.spec.ts`, `failures.spec.ts`, `deep-links.spec.ts` |
| 6 | Two sessions isolated for sources, retrieval, jobs, drafts, deletion | `integration/test_full_flow.py`, `test_profile_api.py`, `test_job_api.py`, `test_generation_editing.py`, `test_clear_data.py`, `test_repositories.py`, `test_retrieval_scoping.py` | | `isolation.spec.ts` |
| 7 | Profile edits mark drafts stale; validation status changes after document edits | `integration/test_generation_create.py`, `test_generation_editing.py`; `unit/test_generation_compose.py` | `pages/workspace/__tests__/WorkspacePage.states.test.tsx`, `documents.test.tsx`, `workspaceModel.test.ts` | `workspace-review.spec.ts` |
| 8 | Empty and long input, invalid schema, provider timeout and 429, database failure, duplicate generation, zero coverage denominator, no evidence | `integration/test_ingest_api.py`, `test_job_api.py`, `test_errors.py`, `test_failure_recovery.py`, `test_generation_failures.py`, `test_generation_create.py`, `test_index_confirm_api.py`, `test_cors_and_body_limit.py`, `test_health.py`, `test_rate_limits.py`; `unit/test_coverage_rules.py`, `test_retrieval_ranking.py`, `test_openai_client.py` | `pages/start/StartPage.test.tsx`, `startForm.test.ts`, `pages/job/__tests__/JobPage.test.tsx`, `jobFormSchema.test.ts`, `generateDraft.test.ts`, `lib/api.test.ts` | `failures.spec.ts` |
| 9 | Records survive a restart; clearing data stops in-flight work from writing them back | `integration/test_full_flow.py`, `test_profile_flow.py`, `test_generation_failures.py`, `test_failure_recovery.py`, `test_ingest_api.py`, `test_index_confirm_api.py`, `test_job_api.py`, `test_clear_data.py`, `test_errors.py` | | |
| 10 | Mobile 375 px, keyboard only, deep-link refresh, source-text escaping, multi-page print | `integration/test_plain_text_and_headers.py` (server half of escaping) | `components/app/components.test.tsx`, `pages/workspace/__tests__/documents.test.tsx`, `panels.test.tsx`, `pages/job/__tests__/JobPage.test.tsx` (markup rendered as text; print target; print gate) | `mobile.spec.ts`, `keyboard.spec.ts`, `deep-links.spec.ts`, `escaping.spec.ts`, `print-long-document.spec.ts` |
| 11 | Optional features | none built, so nothing to test | | |

Limits of what these tests show, from `backend/tests/TEST_MATRIX.md` and from reading the tests:

- The deterministic checks compare words and figures. The tests show the documented rules hold, not that fabrication is impossible.
- "Restart" in the backend tests is a second application instance with its own database client in the same process, not a killed and restarted operating-system process.
- MongoDB's TTL deletion is not waited for. The tests check that the TTL indexes exist and that the application refuses expired sessions itself.
- Backend tests exercise the OpenAI provider only through a stubbed SDK object. Real model behaviour is covered only by the opt-in smoke test.
- Frontend unit tests mock `fetch`; only the Playwright suite runs the frontend against the real API (with the fake provider).
- Print is checked through print-media emulation, layout measurements and the page count of a PDF made by headless Chromium. Nobody has looked at where the page breaks fall; do that by hand in the browser's print preview.

## 13. Not verified

| Item | Status |
|---|---|
| Real OpenAI path (extraction, job analysis, generation, regeneration, verification, embeddings) | Not run for this document. The backend integration pass reported that it had never been called; the smoke test in section 7 is how to check it. Prompt quality, the `MAX_OUTPUT_TOKENS_*` values and latency against `gpt-6-luna` are therefore unverified here. |
| `npm run test -- --run` | A clean run was not observed, see section 11. Re-run it on a machine that is not busy. |
| `python -m pytest tests/integration -q` | Clean in the first and third run; 9 failures in the second, made while source files were being edited, see section 11. The cause of those failures was not established. |
| `npm run build`, `npm run test:e2e` | Not run for this document. |
| Starting the API and the frontend by the commands in section 3 | Not started for this document. The backend integration pass reported a successful start on ports 8000 and 8010 with the fake provider and correct `/healthz`, `/readyz` and `/docs` responses. |
| Setup from a clean checkout | Partly. The backend installation into a new virtual environment and the unit tests from it were run (section 11). `npm ci` and a fresh clone of the repository were not. |
| Experience master file against the retrieved job postings | No real run is recorded here, see section 10. |
| The four live postings | Not run through the application for this document. They are a snapshot of 2026-10-07 and will close or change. |
| `build_frontend_sample.py`, `fetch_live_jobs.py` | Not run for this document. |
| Behaviour on the deployed service | See [DEPLOYMENT.md](DEPLOYMENT.md). |
