# Developer scripts

Tools that are run by hand during development. The application never imports
anything from this folder.

## `evaluate_experience_master.py`

The local real-data evaluation asked for in section 9 of
`IMPLEMENTATION_SPEC.md`: it runs the author's own experience master file
against job postings retrieved from public careers pages, through the real
application and the real AI provider, and reports whether evidence was
retrieved correctly, facts were preserved, gaps were flagged and different jobs
received different emphasis.

**It costs money and it handles real personal data.** Read "Privacy" and
"Budget" below before running it.

### Requirements

- The development dependencies (`requirements-dev.txt`; `python-docx` reads the
  `.docx` file).
- The local MongoDB from `compose.yaml` (`docker compose up -d mongo`).
- `OPENAI_API_KEY` in the environment or the project-root `.env`. The
  application's settings layer loads it; the script never reads or prints it.
- The full text of the live postings under
  `backend/fixtures/jobs/live/_local_full/`. After a fresh clone, download it
  with `python backend/fixtures/jobs/live/fetch_live_jobs.py`.

### Run

From `backend/`:

```bash
EXPERIENCE_MASTER_PATH="../Experience Master.docx" .venv/bin/python -m scripts.evaluate_experience_master
```

| Variable | Default | Meaning |
|---|---|---|
| `EXPERIENCE_MASTER_PATH` | `<repository root>/Experience Master.docx` | The master file: `.docx`, `.txt` or `.md`. A missing file stops the script with a message before anything is called. |
| `MASTER_EVAL_JOBS` | one close, one partial, one stretch posting | Comma-separated slugs of one to three live postings to use instead. |
| `MASTER_EVAL_REPORT_PATH` | `backend/scripts/out/experience_master_report.md` | Where the report is written. |
| `MASTER_EVAL_TRANSCRIPT_DIR` | not set | A folder **outside the repository** that receives `run.json` (every API answer) and `provider_calls.json` (every model input and output). Personal data; delete it afterwards. |
| `MASTER_EVAL_REPLAY` | not set | Path of a saved `run.json`. The checks and the report are recomputed from it; nothing is called and nothing is billed. |
| `MASTER_EVAL_SELFTEST` | not set | `1` runs the script against the fake provider with the fictional sample profile and the synthetic jobs. It checks the script, prints the report instead of writing it, and is not an evaluation result. |
| `TEST_MONGODB_URI` | `mongodb://127.0.0.1:27017` | The MongoDB server for the disposable database. |

Model names, timeouts and output limits come from the normal settings
(`OPENAI_MODEL`, `PROVIDER_TIMEOUT_SECONDS`, ...), exactly as in a deployment.

Exit status: `0` everything ran, `1` at least one step failed (the report says
which), `2` the script could not start, `3` the report was withheld by the
privacy check.

### What it does

1. Reads the master file as plain text. For `.docx`, each paragraph becomes one
   line, a list paragraph gets a `- ` marker and a table row becomes one line.
2. Picks the postings: every JSON under `fixtures/jobs/live/` whose full text is
   in `_local_full/`, one per fit category. With two partial-fit postings the
   Stripe one is used, because it is a general software role and so asks for a
   different emphasis than the two machine-learning roles.
3. Starts the application in-process (`httpx.ASGITransport`, `APP_ENV=test`,
   `AI_PROVIDER=openai`) on a database named
   `resume_tailor_test_master_<random>` and drives it through the HTTP API:
   create session, ingest the master text as one source labelled
   "Experience master", resolve conflicts with `PATCH /api/profile`, confirm,
   then for each posting analyse the job, generate, and open every evidence
   record the draft refers to. Finally `DELETE /api/session`.
4. Counts the documents left in the database, then drops the database, also
   when a step failed or the run was interrupted.

### Checks

| Check | How it is computed |
|---|---|
| Extraction | Records by category, statements, skills; the share of records and statements whose source span equals `original_text[start:end]`; header fields and summaries found verbatim in the master; review flags by kind; how many master lines are found in the profile (by source span, by text, as a header fact, as a contact detail or as skills). The lines that are not found are printed on the terminal. |
| Fact preservation | Every header in the resume (heading, subheading, location, date strings) equals the confirmed profile record it belongs to; every confirmed role is listed once, in profile order; the contact block equals the confirmed one. |
| Numbers | Every number in every statement occurs in the evidence that statement cites. |
| Evidence | Every evidence ID the draft refers to opens through `GET /api/evidence/{id}`; an excerpt with offsets equals that slice of the master. |
| Validation | Statements by validation status and section; omitted claims by section, with their reasons; whether the correction pass ran and which findings triggered it. |
| Coverage | Counts and percentage, recounted from the items; every requirement with its rating. |
| Lacking qualifications | Qualifications the profile demonstrably lacks must not be rated `supported`. Only requirements the job analysis put in the category skill, experience, education or certification are considered (duties and culture statements are not qualifications). One counts as lacking when it asks for N+ years and the confirmed employment dates cover fewer, when it asks for a degree and the profile has no education record, or when every technology it names is absent from the master text (the employer's own name is not such a term; "Machine Learning" counts as present when the master says "ML"). The rule is narrow on purpose; a requirement it misses can still be a gap. |
| Nothing invented | No posting term that is absent from the master appears in the resume or in a cover-letter paragraph that cites evidence. |
| Emphasis | Jaccard overlap, between jobs, of the evidence cited by resume statements, of the evidence retrieved and of the skills listed; bullets per record; which records lead. |
| Latency and usage | Seconds and tokens per provider call, seconds per HTTP step, SDK retries seen. |

The numbers describing the documents' shape (words per bullet, duplicates,
first person, length) inform a reviewer; none of them is a quality score.
Whether the documents read well is judged by a person from the terminal output.

### Checking the script itself

```bash
MASTER_EVAL_SELFTEST=1 .venv/bin/python -m scripts.evaluate_experience_master
```

runs the same flow with the fake provider, the fictional sample profile and
three synthetic jobs. It costs nothing, never reads the master file or the
`.env`, and prints its report instead of writing it. Use it after changing the
script or the API; its output says nothing about real model quality.

### Privacy

The master file is the author's real personal data.

- The terminal output shows the extracted profile and the generated documents.
  Do not paste it into an issue, a commit or a chat.
- The report holds only aggregate numbers, pass/fail flags, the public
  postings' titles, companies and URLs, and requirement texts taken from the
  postings. Records are referred to by category and position ("employment 2").
  Messages written by the application are printed with their quoted terms and
  figures removed; notes written by the model are counted but not printed.
- Before the report is written it is checked against the master text: any run
  of four consecutive words of the master, any contact value, record title or
  organisation name stops the write (exit status 3).
- `backend/scripts/out/` ignores everything except its own `.gitignore`, so the
  report is not committed by accident.
- The application's log lines are captured and checked the same way; the count
  of lines that hold master text is printed (it should be 0).
- Nothing from the master file may be copied into fixtures, documentation,
  tests or the frontend.

### Budget

One run makes at most: one extraction, one confirmation (embedding calls),
three job analyses and three generations. A generation makes one embedding
call and one or two model calls (the draft and the single correction pass the
application allows). The script enforces these limits itself and stops rather
than exceed them.

Nothing is retried. If the provider or the application fails, the failure is
reported exactly as the API answered it, the remaining steps that depend on it
are skipped, and the fake provider is never used in its place. The OpenAI SDK
may retry a request on its own (`PROVIDER_MAX_RETRIES`); the script counts and
reports those retries.

To look at a finished run again without paying for it, save a transcript and
use `MASTER_EVAL_REPLAY`.

---

This documentation and the script were prepared with AI assistance (Claude).
