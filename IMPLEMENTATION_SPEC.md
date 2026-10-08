

## 1. Objective and implementation brief

Build a deployed web application that turns a user's resume/CV, pasted LinkedIn profile, and optional background notes into an editable knowledge base. Given a job description, retrieve relevant evidence and generate a tailored resume and cover letter. Show where each factual claim came from and which requirements lack supporting evidence.

The app should emphasize relevant experience without inventing qualifications. It is a drafting assistant, not an employment eligibility system. Never present coverage metrics as a hiring probability or a guaranteed ATS score.

Implement a complete vertical slice before adding optional features: input → review profile → analyze job → retrieve evidence → generate → review citations → edit → print/export. Do not stop at a UI mockup or an API that only works locally.

Use these decisions unless an existing repository requires a justified adjustment:

| Layer | Decision |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind CSS, shadcn/ui, Lucide icons |
| Frontend data/forms | TanStack Query; React Hook Form and Zod where useful |
| Backend | Python 3.12, FastAPI, Pydantic, Uvicorn |
| Database | MongoDB locally; MongoDB Atlas for deployment |
| Database driver | Current supported PyMongo API; avoid blocking database calls on the async event loop |
| Generation | OpenAI Python SDK, server-side structured-output validation; default `gpt-6-luna`, configurable alternative `gpt-5.6-terra` |
| Embeddings | OpenAI `text-embedding-3-small`; one consistent model/dimension per index |
| Initial retrieval | Stored embeddings in MongoDB; bounded per-profile cosine ranking in Python |
| Retrieval upgrade | MongoDB Atlas Vector Search, behind the same retrieval interface |
| Deployment | Render Static Site for frontend; Render Python Web Service for backend; Atlas for database |
| Tests | pytest, frontend Vitest/Testing Library, Playwright critical-path tests |

Pin compatible dependencies and commit lockfiles. Avoid introducing a large agent orchestration framework for this small pipeline. Provider/model selection must be configurable, not scattered through application code. Include a deterministic fake provider for tests; clearly label demo mode in the UI.

Use OpenAI models for extraction, job analysis, generation, and optional verification. Prefer the low-cost `gpt-6-luna`; allow `gpt-5.6-terra` through configuration when evaluating quality/cost tradeoffs. These exact model IDs have official documentation: [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) and [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra). Use [text-embedding-3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small) for embeddings. Verify account access and supported API parameters during implementation; do not silently switch to a more expensive model. Centralize SDK calls, enforce output limits, cache unchanged embeddings, and record usage without private content. Do not hard-code current prices into business logic.

The project's root `.env` will contain `OPENAI_API_KEY`. Load it explicitly from the project root regardless of the working directory. Never print, commit, copy into documentation, or expose the value to the frontend. No API-key file needs to be created by the implementing agent if Alan has already supplied it. Deployment uses Render secret environment variables with the same names rather than uploading `.env`.

## 2. Core functional requirements

### A. Profile ingestion and review

- Accept pasted resume/CV text, pasted LinkedIn profile text, and optional background notes, each with a source label. Automated LinkedIn scraping is not part of the core.
- Explain before submission that profile content goes to the configured AI provider and is temporarily stored by this application. Offer fictional sample data.
- Normalize whitespace but retain original text and source offsets. Extract contact details, employment, education, projects, achievements, skills, and certifications into a structured draft.
- Every extracted factual record needs a source reference. If ambiguous, mark it for review rather than guessing dates, employers, or proficiency.
- Show editable grouped records. Require user confirmation before indexing/generation; edited statements become new user-provided evidence with provenance.
- Deduplicate repeated information without merging distinct roles. Do not silently resolve conflicting dates or claims: show conflicts to the user.
- On confirmation, create versioned evidence records and embeddings. Display indexing progress and recoverable failure states.
- Core supports one active profile per anonymous session. Persist that session's profile and generated drafts in MongoDB; a multi-profile library is optional.

### B. Job analysis

- Accept job-description text plus optional company and role title. URL fetching is optional.
- Extract unique requirements with IDs, normalized text, category, importance, and a supporting span from the job description. Separate explicitly required and preferred qualifications; flag inferred requirements.
- Users may correct extracted requirements before generation. Treat the job description as untrusted data, never as system instructions.
- Reject empty/oversized input with useful messages. Initial proposed limits: 60,000 characters total profile text and 25,000 job-description characters; keep limits configurable.

### C. Tailored resume and cover letter

- Retrieve evidence for each requirement and the overall role. Generate structured resume sections and cover-letter paragraphs from that evidence and confirmed profile metadata.
- Preserve exact employer names, job titles, dates, degrees, certifications, and numerical results unless the user explicitly changes the profile.
- Reorder, shorten, and rephrase supported experience. Never transform familiarity into professional expertise or a personal project into employment.
- Contact/education/history metadata should come from confirmed fields; do not rely on semantic retrieval to remember mandatory identity information.
- Every factual generated bullet/paragraph carries evidence IDs. Non-factual connective language can be marked `not_applicable` for provenance.
- Show a resume tab, cover-letter tab, and evidence/coverage tab. Allow editing, targeted regeneration, copy, and browser print/save-as-PDF.
- Manual document edits are marked user-edited and need revalidation. Do not retain a green validation badge automatically after an edit.
- Print output is single-column, selectable text, readable headings, and sensible page breaks. Evidence badges, controls, and analysis panels are excluded from the submitted document layout.

### D. Evidence and coverage

- Clicking an evidence badge opens the exact supporting excerpt, source label, and associated role/project. Do not expose internal embedding vectors.
- Classify each requirement as supported, partially supported, missing, or uncertain. Show the rationale and evidence. A missing retrieval result means no evidence found in the supplied profile, not proof that the user lacks that skill.
- If displaying coverage, calculate `100 × (supported + 0.5 × partial) / assessed_requirements`; exclude uncertain requirements and show their count. Handle a zero denominator as unavailable. Use equal weights initially and show counts alongside the metric.
- Label this as evidence coverage, not job suitability, ATS compatibility, or hiring probability. Allow corrections; do not fabricate evidence to improve the metric.

### E. Privacy, isolation, and graceful failure

- Public deployment must isolate visitors. Create a cryptographically random anonymous bearer token; store only its hash on the server. Keep the client token in sessionStorage, never in URLs, logs, or analytics. Session access ends on expiry; explain that closing the tab can lose access.
- Resolve ownership from the authenticated token, never from a client-supplied owner ID. Scope every read/write/retrieval/delete to that owner. Random document IDs alone are not authorization.
- Default expiry: 24 hours, with `expires_at` on all owned records. Deny expired access immediately; MongoDB TTL cleanup may occur later. Persistent accounts and long-term history are optional.
- Provide “Clear my data” with a confirmation dialog. Delete the session's sources, profile, evidence/embeddings, jobs, drafts, and optional tracker records. Revoke the session first and prevent in-flight operations from recreating deleted data.
- Use server-side API keys, exact CORS origins, request/input limits, provider timeouts, capped retries, and rate/cost controls. Use persistent request counters for public AI endpoints rather than relying only on process memory.
- Render source text as plain text; never execute generated HTML. Treat uploaded content and job text as untrusted. Ignore instructions embedded in them. Strip contact details from embedding inputs where they are irrelevant.
- Log request IDs, status, duration, and provider usage only; redact tokens, connection strings, source text, and generated personal documents.
- On provider/database failure, preserve completed work, show a retry action, and never silently return fake results as real generation.

## 3. RAG architecture

### Pipeline

```text
Labeled source text
  → extraction + source-span validation
  → user-reviewed structured profile
  → versioned evidence records
  → embeddings stored in MongoDB

Job description
  → reviewed requirements
  → requirement/query embeddings
  → owner/profile-version-scoped retrieval
  → selected evidence + mandatory profile metadata
  → structured LLM generation
  → provenance/factual validation
  → editable documents + evidence + coverage
```

### Evidence records and indexing

Prefer semantic records over arbitrary chunks: one achievement, project description, role summary, education item, or certification. Split long records at paragraph/sentence boundaries, preserving parent record and source references. Starting target: 100–250 words per evidence chunk, with only enough overlap to preserve context. Tune against fixtures, not arbitrary token counts.

Each record stores `evidence_id`, `owner_id`, `profile_id`, `profile_version`, `source_id`, `source_revision`, source span or user-edit provenance, original excerpt, normalized text, category, skills/tags, embedding model, dimension, content hash, embedding, and timestamps. Include `embedding_status` and `expires_at`.

Cache embeddings using owner-scoped content hashes plus model/version. Never mix vectors from incompatible models or dimensions. Profile changes increment the version, invalidate changed evidence, and mark old drafts stale. Do not generate against partly indexed or mixed-version evidence.

### Retrieval

Implement `retrieve(owner, profile_version, query, filters, limit)` behind an interface:

1. Embed each job requirement and a concise overall role query.
2. Load only confirmed evidence for the current owner and profile version. Bound the initial profile to 200 chunks; ask users to reduce input if exceeded.
3. Calculate cosine similarity in Python, handling zero vectors. Combine semantic rank and explicit keyword/skill overlap with a documented heuristic or rank fusion.
4. Take roughly 3–5 candidates per requirement, deduplicate by evidence ID, retain diverse relevant roles/projects, and select a final bounded context of about 12–20 records within the configured token budget.
5. Keep retrieval scores internal or label them similarity scores; they are not confidence probabilities. Similarity thresholds require fixture-based calibration and cannot alone decide whether a qualification is supported.
6. Pass only retrieved evidence plus mandatory confirmed metadata to generation. Return the retrieved IDs and profile version for auditability.

This is genuine retrieval-augmented generation even when ranking runs in Python: documents are indexed, relevant evidence is selected, and selected context grounds generation. Do not claim the core uses Atlas Vector Search unless it actually does. Atlas Vector Search is the optional scaling path; owner/profile/version filters must be applied during retrieval, not merely after a broad cross-user search. Match index dimensions to the configured embedding model.

### Generation contract

Use separate provider operations for extraction, job analysis, document generation, and optional semantic verification. Keep prompt templates in version-controlled files. Explicitly distinguish trusted instructions from quoted user/source content.

Require schema-valid JSON containing:

```text
profile_version, job_version, generation_id
resume: contact, summary, experience[], projects[], education[], skills[]
cover_letter: paragraphs[]
each factual item: text, evidence_ids[], validation_status, warnings[]
coverage[]: requirement_id, status, evidence_ids[], rationale
```

Do not accept arbitrary model-created evidence IDs. Validate all IDs against the owner's retrieved context. Confirm that extraction spans exist in the original source; invalid spans go to manual review.

### Grounding validation and limits

- Validate schema, IDs, ownership, version, and required evidence links deterministically.
- Check names, dates, degree/certification labels, and numbers against structured facts/source excerpts. Numerical checking must include units and context: “20%” elsewhere is not proof of a claimed 20% reduction.
- Reject known contradictions and invented qualification statements. Allow one bounded regeneration with concrete validation feedback.
- Semantic support is harder than citation existence. An optional verifier may classify claims, but never treat its judgment as a guarantee. Mark unresolved claims `needs_review`, show why, and require user review before export. Default to omitting unsupported factual claims.
- Track validation status per claim: `supported`, `needs_review`, `unsupported`, `user_edited`, or `not_applicable`. Document that automated validation reduces fabrication risk but cannot eliminate it.

## 4. NoSQL data model

Use Pydantic application schemas and MongoDB indexes. Store UTC timestamps and ISO strings in API responses. Store small text/JSON documents in MongoDB; original large uploads belong in object storage if that optional feature is built.

| Collection | Main fields |
|---|---|
| sessions | token_hash, owner_id, created_at, expires_at, revoked_at, quota counters |
| sources | owner_id, profile_id, label, source_type, text, revision, hash, expires_at |
| profiles | owner_id, confirmed structured facts, version, review/index state, expires_at |
| evidence | owner_id, profile_id/version, provenance, text, tags, embedding metadata/vector, expires_at |
| jobs | owner_id, description, company/title, reviewed requirements, version, expires_at |
| generations | owner_id, profile/job versions, retrieved IDs, documents, coverage, warnings, usage, expires_at |
| applications (optional) | owner_id, company/role, job_id, generation_id, status, dates, notes, expires_at |

Index token hashes uniquely; index evidence by owner/profile/version; index jobs and generations by owner/date. Add TTL indexes to expiring collections. Version keys and owner fields are mandatory, including on optional objects. Do not write personal documents to the backend filesystem for persistence.

## 5. API contract

All `/api` endpoints except session creation require a bearer token. Return consistent errors: `{error: {code, message, request_id, field_errors?}}`. Validate typed request/response bodies and ownership. Prefer 404 for missing or non-owned object IDs. Implement the following core routes:

| Method/path | Behavior |
|---|---|
| GET /healthz | Process liveness; no sensitive configuration |
| GET /readyz | Database ping and configuration readiness; no billable provider calls |
| POST /api/sessions | Issue an anonymous token and expiry; rate limited |
| DELETE /api/session | Revoke session and delete all owned records |
| POST /api/profiles/ingest | Store labeled text and return extracted profile draft |
| GET /api/profile | Retrieve current owned profile and review/index status |
| PATCH /api/profile | Save reviewed edits; use expected-version conflict detection |
| POST /api/profile/confirm | Confirm revision, build evidence/embeddings, report success or recoverable failure |
| GET /api/evidence/{id} | Return supporting source text/provenance for an owned record |
| POST /api/jobs | Analyze job text and return editable requirements |
| PATCH /api/jobs/{id} | Save reviewed requirements and increment job version |
| POST /api/generations | Retrieve and generate using job ID/current profile; idempotency key required |
| GET /api/generations/{id} | Return draft, coverage, citations, and stale status |
| PATCH /api/generations/{id} | Save manual edits and invalidate relevant validation |
| POST /api/generations/{id}/validate | Revalidate user edits without silently regenerating them |

Use MongoDB uniqueness/state records to prevent duplicate paid generation for repeated idempotency keys. Begin with bounded synchronous operations and honest progress labels such as “Generating”; do not show simulated completion percentages. If workloads exceed safe request duration, add a durable worker/job queue as an optional infrastructure upgrade rather than relying on fragile in-process background work.

## 6. Frontend design

Use actual shadcn/ui components throughout the interactive UI, with a restrained neutral palette, one accent color, readable typography, clear spacing, and light/dark themes if convenient. Avoid decorative dashboards that obscure the main workflow. Configure Vite/Tailwind using the current [shadcn/ui Vite setup](https://ui.shadcn.com/docs/installation/vite).

Screens:

1. **Start:** concise value proposition, privacy/retention notice, paste inputs, and “Try sample profile.”
2. **Profile review:** grouped editable cards for roles, projects, education, and skills; source excerpts; conflict badges; confirm action.
3. **Target job:** role/company inputs, job textarea, reviewable requirement list.
4. **Workspace:** desktop two-pane layout with editable resume/cover letter on the left and evidence/coverage on the right. On mobile, use tabs and a source drawer.

Suggested components: Card, Button, Input, Textarea, Tabs, Badge, Accordion, Sheet, Dialog, Alert, Skeleton, Select, Tooltip, and Toast. Use a Table/Kanban only if the optional tracker is implemented.

Provide visible loading, empty, success, stale, and error states. Disable duplicate submissions, retain input after failures, support keyboard navigation and labeled fields, and avoid color-only statuses. Evidence badges remain separate from printable text. No horizontal overflow at 375px width; destructive actions require explicit confirmation. Optional controls should appear only when implemented, not as misleading enabled buttons.

## 7. Optional feature backlog

Implement in this order only after the core passes acceptance tests and has a working deployment. Each optional feature must have a complete usable flow or remain hidden.

| Priority | Feature | Scope and acceptance condition |
|---|---|---|
| P1 | PDF resume upload | Validate signature/type and configurable size limit (initially 5 MB), extract text, preserve page provenance, show extraction preview. Explain scanned/encrypted/empty PDF failures; OCR is a separate extension. Delete temporary files immediately. |
| P1 | Saved profile/document library | Multiple named profiles, draft history, restore, rename, delete, and export. Persistent cross-device access requires authentication first; anonymous history remains session-scoped and expiring. |
| P1 | Application tracker | Company, role, job link/text, tailored-document links, applied date, status, notes; statuses Saved/Applied/Screening/Interview/Offer/Rejected/Withdrawn. Edit/filter/search; no automatic submissions. |
| P1 | Interview preparation | Job-specific technical/behavioral questions linked to requirements; suggested STAR outlines grounded in actual evidence. For gaps, suggest preparation topics rather than invented stories. |
| P2 | Comparison and revisions | Side-by-side tailored documents for two roles, change summaries, evidence differences, version history, and comparison with original wording. |
| P2 | More templates and exports | Additional accessible resume layouts, DOCX export, direct PDF download. Verify pagination and selectable text; retain print export as fallback. |
| P2 | More transparent metrics | Keyword coverage, supported required/preferred counts, document length, unsupported-claim count. Publish formulas and limits. No fabricated universal ATS or probability scores. |
| P2 | Atlas Vector Search | Replace bounded Python ranking with indexed retrieval; retain owner/version filters and prove parity with fixtures. Verify available cluster capabilities before provisioning. |
| P2 | Accounts and longer retention | Use established authentication, explicit retention choices, per-user authorization, and deletion. Do not roll custom password handling. |
| P3 | DOCX/multiple-file ingestion and OCR | Document-specific parsers, extraction preview, deduplication, and source provenance; reject unsupported formats usefully. |
| P3 | Suggestions and infrastructure | Evidence-backed skill-gap learning suggestions, streamed generation, durable background processing, and cost/latency monitoring. None may invent experience or apply to jobs automatically. |

## 8. Repository and configuration

```text
resume-tailor/
  IMPLEMENTATION_SPEC.md
  .env                      # local secrets; ignored, never committed
  .env.example              # placeholder configuration only
  <experience master file>  # Alan will add sample profile data here
  frontend/                 # package.json, lockfile, src/, tests/
  backend/
    app/main.py
    app/api/ app/schemas/ app/repositories/
    app/services/{ingestion,retrieval,generation,validation}.py
    app/providers/ app/prompts/
    tests/ fixtures/
    requirements.txt requirements-dev.txt
  compose.yaml              # local MongoDB, loopback port binding
  render.yaml               # two Render services, no secret values
  docs/                     # architecture, test/deployment instructions
  README.md                 # student-written final README
  prompt_log.md             # real prompts and process evidence
  .gitignore
```

Provide a project-root `.env.example` with placeholders. Backend configuration: `APP_ENV`, `MONGODB_URI`, `MONGODB_DATABASE`, `CORS_ORIGINS`, `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-6-luna`, `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`, `RETRIEVAL_MODE=python`, `SESSION_TTL_HOURS=24`, request limits, and provider timeout/quota settings. `OPENAI_MODEL=gpt-5.6-terra` is the supported alternative. Both generation and embedding clients use `OPENAI_API_KEY`. Validate configuration on startup; embedding dimensions must match recorded model metadata. Environment variables override the root `.env`; derive its path from the application location, not the current working directory. Keep fake providers confined to explicit test/demo mode.

Frontend: `VITE_API_BASE_URL` only for the public backend address. No secrets may have the `VITE_` prefix. Ignore `.env`, uploads, local databases, real resumes, recordings with personal data, and credentials. Commit fictional fixtures and template env files only.

## 9. Local setup and testing instructions to deliver

### Experience master file and job-description fixtures

Alan will add an **experience master file in the project root** containing sample candidate information. Before designing fixtures, locate and read this file using its actual filename; do not assume a specific extension or create a substitute pretending to be Alan's data. Add a configurable `EXPERIENCE_MASTER_PATH` to the test/seed tooling. If the file is not yet present, continue independent development with clearly fictional fixtures and report that sample-profile validation remains pending.

Use the master file as the authoritative sample profile for ingestion, profile review, retrieval, resume/cover-letter generation, coverage, and interview-question tests. Preserve the original file. Derive expected facts/evidence from it without embellishment, and run at least two different target roles against the same profile. Do not bundle the file into the public frontend or commit it by default; only publish a sanitized fictional fixture. User-facing sample mode must use that sanitized fixture.

Once the sample profile is available, **retrieve 3–5 relevant public job descriptions for testing**, preferably from employers' official careers pages. Pick a close-fit role, a partial-fit role, and a stretch role with explicit missing qualifications. Search using broad role/skill keywords derived from the sample; do not send private profile text or contact details to search engines. Record title, company, source URL, date retrieved, and the selected requirements in `backend/fixtures/jobs/`. Honor access restrictions; use an accessible alternative if a page requires login. This is developer-side test-data collection, not a job-URL-import feature in the product.

Keep only permitted test excerpts and provenance in distributable fixtures; do not assume permission to republish entire listings. Derive sanitized synthetic job fixtures for deterministic CI and label them as synthetic. Preserve distinctions between live source excerpts and synthetic fixtures. Test the real sample profile against retrieved descriptions locally; record whether evidence was retrieved correctly, facts were preserved, important gaps were flagged, and different jobs produced appropriately different emphasis. Do not fabricate retrieved jobs or report unexecuted tests as passed.

The implementing agent must supply executable scripts matching these commands and document prerequisites (Python 3.12, supported Node LTS, optional Docker). Verify instructions from a clean checkout. These are prescribed commands, not commands already tested by this spec.

### Start services

From repository root, create configuration only if `.env` does not already exist, add the locally supplied API key without printing it, and start the loopback-only MongoDB service:

```bash
test -f .env || cp .env.example .env
docker compose up -d mongo
```

If Docker is unavailable, use a dedicated Atlas development database with a restricted IP access list. Never point automated tests at the live database.

Backend terminal:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
# The settings loader reads ../.env; use a development DB and fake providers first.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Load the project-root `.env` through the app's settings layer; merely copying it is not sufficient unless the loader is implemented. Never overwrite Alan's existing `.env`. For real smoke tests, enable the OpenAI adapter explicitly and use the supplied key with bounded requests.

Frontend terminal:

```bash
cd frontend
npm ci
cp .env.example .env.local
# Set VITE_API_BASE_URL=http://127.0.0.1:8000
npm run dev -- --host 127.0.0.1
```

Fix the frontend development port to 5173 or document its actual value. Allow the exact development origin in CORS. Check `/healthz`, `/readyz`, API docs, and the browser workflow.

### Automated checks

Backend commands, from `backend/`:

```bash
python -m pytest tests/unit -q
python -m pytest tests/integration -q
```

Frontend commands, from `frontend/`:

```bash
npm run lint
npm run typecheck
npm run test -- --run
npm run build
npx playwright install chromium
npm run test:e2e
```

Configure Playwright to start isolated test services or target explicitly started local services. Integration tests use a disposable database with a safety guard requiring a test database name. Fake generation/embeddings make CI deterministic and avoid billing. Real provider smoke tests are separately opt-in, use fictional input, and report actual failures instead of falling back to mocks.

Required meaningful tests:

- Ingest sources, edit/confirm profile, index, analyze job, generate, inspect evidence, edit/revalidate, print, clear data.
- A job asks for Kubernetes; profile mentions Docker only: no invented Kubernetes experience.
- A source contains a 20% metric unrelated to the generated claim: reject the misleading numerical reuse.
- Instructions hidden in resume/job text cannot override grounding rules or access other profiles.
- Unknown evidence ID, cross-user ID, expired/revoked token, mixed profile versions, and unindexed edits are rejected.
- Two sessions remain isolated for sources, retrieval, jobs, drafts, and deletion.
- Profile edits mark old drafts stale; validation status changes after document edits.
- Empty/long input, invalid schema, provider timeout/429, database failure, duplicate generation, zero coverage denominator, and no evidence results have useful behavior.
- Restart backend and confirm non-expired MongoDB records survive; clearing data prevents concurrent generation from writing them back.
- Mobile 375px, keyboard-only interaction, deep-link refresh, source-text escaping, and multi-page print layout work.
- Optional features receive relevant parser, ownership, persistence, and failure tests when built.

Maintain fictional fixtures with expected evidence/requirements for CI, plus local tests derived from the experience master file and retrieved job descriptions. Evaluate real generation for supported claims, omitted important facts, citation correctness, and consistency across two jobs; do not require byte-identical LLM outputs. Include an experience-master seed/test command, its required path setting, and a short report of actual results.

## 10. Render deployment plan

Prepare `render.yaml` and a manual runbook. Do not claim deployment success until the public application works. Do not commit credentials or automatically provision paid resources.

### Database

Create an Atlas database and least-privilege application database user. Set its connection URI only in backend secrets. Allow the developer's current IP for local use and the backend service's documented outbound addresses for deployment; do not default to unrestricted network access. Atlas requires clients to match its [IP access list](https://www.mongodb.com/docs/atlas/security/ip-access-list/).

The core uses normal MongoDB collections plus Python retrieval. Atlas vector-index provisioning is unnecessary unless the optional vector-search backend is selected. Its vector index must include the embedding field and owner/profile-version filters; see [MongoDB Vector Search](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/).

### Backend web service

| Setting | Value |
|---|---|
| Root directory | `backend` |
| Runtime | Python, pinned to the tested version |
| Build | `pip install -r requirements.txt` |
| Start | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health-check path | `/healthz` |
| Secrets | MongoDB URI and `OPENAI_API_KEY` (same key used for generation and embeddings) |
| Nonsecret settings | Models, DB name, exact frontend CORS origin, limits, retention |

Use the application's actual import path; the bind address/port pattern follows [Render's FastAPI deployment guide](https://render.com/docs/deploy-fastapi). Never use `--reload` in production. Initialize indexes idempotently at startup, without deleting collections.

### Frontend static site

- Root directory: `frontend`; build: `npm ci && npm run build`; publish directory: `dist`.
- Set `VITE_API_BASE_URL` to the backend HTTPS URL before building. It is a build-time public value, so changing it requires rebuilding.
- Add SPA rewrite `/*` → `/index.html`, action Rewrite. Verify nested-route refreshes. See [Render Static Sites](https://render.com/docs/static-sites) and [rewrite guidance](https://render.com/docs/redirects-rewrites).
- Once the static-site URL exists, set the backend's exact CORS origin to it and redeploy as necessary. Do not use wildcard CORS as an authorization substitute.

### Persistence and cold starts

Store profiles, vectors, sessions, and documents in Atlas. Use temporary disk only for transient parsing and clean it up. Render free services can sleep after 15 minutes idle, take around a minute to wake, and lose local file changes on restart/redeploy; design loading/retry behavior accordingly. [Render free-service limitations](https://render.com/docs/free)

### Deployment verification and recovery

1. Record deployed commit, service URLs, environment names (not values), and build status.
2. Check live health/readiness, exact CORS behavior, and database access.
3. In an incognito browser, complete the entire flow with fictional data using real configured providers; confirm it is not demo mode.
4. Confirm source links, coverage, manual editing, print output, mobile view, and clear-data behavior.
5. Repeat with a second session and prove data isolation. Check that no private data or secret appears in frontend bundles, repository, URLs, or logs.
6. Test backend restart/cold start, deep links, and persistence. Log any unverified behavior explicitly.
7. If a deployment fails, inspect build/runtime logs, module paths, env settings, Atlas network access, and API base URL. Roll back to the last working commit when appropriate; keep database changes additive/versioned so rollback remains possible.

## 11. Build order and definition of done

1. Scaffold the monorepo, schemas, env configuration, MongoDB access, session isolation, sample fixtures, and frontend shell. Deploy health/frontend early.
2. Complete paste ingestion, source-linked extraction, profile review, and embeddings.
3. Complete job analysis, scoped retrieval, structured generation, validation, and evidence UI.
4. Add editing, coverage, print output, deletion, limits, and failure handling.
5. Run the critical tests locally and on Render; correct failures before optional work.
6. Add optional features in priority order only when core acceptance remains green.

Core is done only when a new visitor can complete the live workflow, all factual claims show inspectable provenance or a clear review warning, MongoDB persistence/isolation work, secrets remain server-side, print output is usable, and the clean-checkout test/run instructions work. Supply the public app/API URLs, repository, deployed commit, test results, known limits, and an explanation of retrieval and grounding. Do not present placeholders as completed features.
