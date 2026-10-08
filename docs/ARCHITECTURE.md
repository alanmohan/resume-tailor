> AI-generated documentation (Claude Code, 2026-10-07). Reviewed and owned by the repository author.

# Resume Tailor: architecture

This document describes what the code in this repository does, as read from the source on 2026-10-07. File paths are relative to the repository root. Related documents: [API.md](API.md), [TESTING.md](TESTING.md), [DEPLOYMENT.md](DEPLOYMENT.md).

Contents

1. [System overview](#1-system-overview)
2. [The pipeline, step by step](#2-the-pipeline-step-by-step)
3. [Why this is retrieval-augmented generation](#3-why-this-is-retrieval-augmented-generation)
4. [Data model](#4-data-model)
5. [Sessions: isolation, expiry and deletion](#5-sessions-isolation-expiry-and-deletion)
6. [AI providers, the fake provider and demo mode](#6-ai-providers-the-fake-provider-and-demo-mode)
7. [Untrusted input](#7-untrusted-input)
8. [Limits, quotas and cost controls](#8-limits-quotas-and-cost-controls)
9. [Errors, logging and middleware](#9-errors-logging-and-middleware)
10. [Frontend](#10-frontend)
11. [Known limitations](#11-known-limitations)

## 1. System overview

Resume Tailor turns pasted resume, LinkedIn and notes text into a reviewed profile, then drafts a resume and cover letter for one job description using only evidence retrieved from that profile. Every generated statement carries the IDs of the evidence it rests on, and every job requirement gets a coverage rating. It is a drafting assistant; it does not score candidates.

```text
Browser (React single-page app, static files)
   |  HTTPS, JSON, "Authorization: Bearer <session token>"
   v
FastAPI application (one Python process, uvicorn)
   |-- app/api/           routes: HTTP <-> service calls
   |-- app/services/      ingestion, indexing, retrieval, drafting, validation, coverage
   |-- app/repositories/  the only code that queries MongoDB; every query filters on owner_id
   |-- app/providers/     AIProvider protocol; OpenAI implementation; deterministic fake
   |-- app/prompts/       version-controlled prompt templates (Markdown)
   v                          v
MongoDB                     OpenAI API
(Docker locally,            (Responses API with strict structured output;
 Atlas when deployed)        text-embedding-3-small for vectors)
```

| Layer | Implementation | Where |
|---|---|---|
| Frontend | React 19, TypeScript, Vite 8, Tailwind CSS v4, shadcn/ui components, TanStack Query 5, react-router, react-hook-form + zod, jsPDF (PDF download) | `frontend/` |
| Backend | Python 3.12, FastAPI, Pydantic v2, pydantic-settings, uvicorn | `backend/app/` |
| Database | MongoDB through PyMongo's asyncio client (`AsyncMongoClient`), so database calls do not block the event loop | `backend/app/db.py`, `backend/app/repositories/` |
| Generation | OpenAI Python SDK, `responses.parse` with a Pydantic model as strict JSON schema; default model `gpt-6-luna`, alternative `gpt-5.6-terra` | `backend/app/providers/openai/` |
| Embeddings | `text-embedding-3-small`, 1536 dimensions | same |
| Ranking | Exact cosine similarity in numpy plus keyword overlap, fused by reciprocal rank | `backend/app/services/retrieval.py` |
| Deployment | Render web service (API) + Render static site (frontend) + MongoDB Atlas | `render.yaml`, [DEPLOYMENT.md](DEPLOYMENT.md) |

Exact dependency versions are pinned in `backend/requirements.txt`, `backend/requirements-dev.txt` and `frontend/package-lock.json`. No agent or orchestration framework is used; the pipeline is ordinary function calls.

The application process keeps no state between requests other than the database client and the provider client. Nothing is written to the local filesystem.

Not part of the running application: `backend/tests/` and `frontend/e2e/` (tests), `backend/fixtures/` (fictional test data and its scripts) and `backend/scripts/` (a developer-side evaluation command). Nothing under `backend/app/` imports from them. See [TESTING.md](TESTING.md).

### The four screens and the calls behind them

| Screen (route) | What the user does | API calls |
|---|---|---|
| Start (`/`) | Reads the privacy notice, pastes up to three texts or loads the fictional sample, submits | `POST /api/sessions`, `POST /api/profiles/ingest` |
| Profile review (`/profile`) | Edits records, resolves conflicts, confirms | `GET/PATCH /api/profile`, `POST /api/profile/confirm` |
| Target job (`/job`) | Pastes a job description and presses "Tailor my resume": the job is analyzed and the draft generated in one run, with a two-step progress list. `/job?job=<id>` shows a stored job read-only with "Generate a new draft" | `POST /api/jobs`, `GET /api/jobs/{id}`, `POST /api/generations` |
| Workspace (`/workspace/:generationId`) | Reads both documents, opens citations, edits, regenerates one statement, revalidates, corrects coverage, reads the job's requirements, copies or downloads a PDF | `GET/PATCH /api/generations/{id}`, `.../validate`, `.../items/{item_id}/regenerate`, `GET /api/evidence/{id}`, `GET /api/jobs/{id}` |

## 2. The pipeline, step by step

```text
Labelled source text
  -> extraction (model) + source-span validation (server)      2.1
  -> profile reviewed and edited by the user                   2.2
  -> versioned evidence records                                2.3
  -> embeddings stored in MongoDB, with a per-owner cache      2.4

Job description
  -> requirements (model), checked by the server               2.5
  -> one query per requirement + one role query, embedded      2.6
  -> ranking scoped to owner + profile + version               2.6
  -> bounded evidence context + confirmed profile metadata     2.7
  -> structured generation (model)                             2.7
  -> deterministic validation of every statement               2.8
  -> one correction pass, then removal of unsupported claims   2.9
  -> coverage per requirement                                  2.10
  -> editable draft: edit, revalidate, regenerate one item     2.11
```

### 2.1 Ingestion and source-span validation

Code: `backend/app/services/ingestion.py`, `backend/app/services/textutil.py`. Route: `POST /api/profiles/ingest`.

1. Limits are checked first: at most `MAX_SOURCES` sources (422) and at most `MAX_PROFILE_CHARS` characters in total (413). Then one `ingest` quota unit and one AI call are charged.
2. The **original text of each source is stored untouched** (`sources` collection). `normalize_whitespace` builds a second copy with collapsed whitespace and, for every character of that copy, its index in the original (`NormalizedText.offsets`).
3. The model receives the normalised copies as JSON data under the aliases `S1`, `S2`, ... and returns `LLMExtraction` (`backend/app/providers/extraction_models.py`): contact items, records and conflicts. It returns **verbatim quotes, never character offsets**.
4. `build_draft` checks the model's output before anything is stored:
   - `locate_quote` finds each quote in the source, first exactly in the normalised text, then with a tolerant comparison (letter case, curly versus straight quotes, dash style, line breaks versus spaces). The match is mapped back to offsets in the original, giving a `SourceRef` with `excerpt == original[start:end]`.
   - A quote that cannot be located flags the item `needs_review` with the reason `source span not found`. It is never accepted silently.
   - Title, organisation, location, dates and summary must each occur in the source the record is attributed to; otherwise the record is flagged with the field named.
   - A skill is kept only if it occurs as a whole term in some source ("Java" is not found inside "JavaScript"). A skill that is nowhere in the text is left out and the record is flagged.
   - A whole labelled line returned as one skill ("Backend & Cloud: Python, Flask, AWS S3.") is split into the skills it lists (`skill_items`); the category label is dropped. A label that qualifies the skills ("Coursework only: ...") and a line with a single item stay whole. The real model returned such lines for a profile that writes its technologies under each role; printed on a resume they read as sentences.
   - A contact value is kept only if it occurs in its source. The first verifiable value per field wins; a name, e-mail address or phone number that a later source gives differently becomes a conflict (see step 5).
   - A statement quoted from a line that addresses an AI system (see [section 7](#7-untrusted-input)) is left out, with a note on the record.
   - An employment or education record without any date is flagged; a record the model marked ambiguous is flagged with the model's notes. Dates are stored exactly as written and are never normalised or guessed.
5. **Duplicates and conflicts.** Two extracted records are the same record only if category, title and organisation match (ignoring case and spacing). Then:
   - dates agree, or one side has none: merged quietly;
   - dates differ and the two records come from different sources: merged, and each differing date field becomes a `Conflict` listing both values with their source references;
   - dates differ within one source: kept as separate records (two periods in the same job).
   Conflicts reported by the model are listed as well. Each is attached to the records its quotes were found in; the record positions the model counted are used only when a quote cannot be traced. Nothing resolves a conflict automatically.
   - **One entry per disagreement.** When the server finds a date conflict that the model also reported (same record, and the model's entry already shows every value, whatever field label or date format it used), the two are merged: the model's description is kept, and the field name, record IDs and values are the server's exact ones.
   - **Contact details.** A name, e-mail address or phone number that differs between two sources becomes a conflict with field `contact_name`, `contact_email` or `contact_phone` and no record. Values are compared by meaning (e-mail ignoring case, phone by digits with or without a country code, a name with or without a middle name), and two values inside one source are not a conflict. Like every conflict it blocks confirmation until resolved or dismissed.
6. **Notices.** The draft carries non-blocking `notices` (`ProfileNotice {code, message}`):
   - `source_text_not_captured`, one per source: lines of the pasted text of which less than half was quoted by any header, statement, summary or contact value, and that do not merely repeat stored facts. Headings and labels (fewer than three content words) and lines addressed to an AI system are skipped. The message shows the first three lines (120 characters each) and counts the rest. This exists because the real model once dropped the result lists of two publications without any flag.
   - `missing_name`, `missing_contact_details` (neither e-mail nor phone), `missing_education`. They are recomputed on every `PATCH /api/profile`, so adding the detail removes the notice.
7. Nothing is written until the provider has answered. The profile is written conditionally on its version, so an edit made while extraction was running causes `409 version_conflict` and leaves everything as it was. The post-write guard ([section 5](#5-sessions-isolation-expiry-and-deletion)) runs in a `finally` block.

Ingesting again replaces the sources and the draft, keeps `profile_id` and increases `version`.

### 2.2 Review

Code: `backend/app/services/profile_service.py`. Route: `PATCH /api/profile`. No AI call.

The request carries the whole reviewed profile and the version it was based on. The server compares it with the stored profile item by item:

- changed text: provenance becomes `user_edited`, the review flag is cleared, and the original `source_ref` is kept for reference only;
- an item without an ID: new, provenance `user_added`;
- unchanged: provenance, source reference and review flag stay.

Conflict decisions (`resolved` or `dismissed`, with an optional note) are applied only when sent explicitly. Any change increases `version` and resets the profile to `status: draft`, `index_state: not_indexed`, `indexed_version: null`. A request that changes nothing returns the stored profile at the same version, so saving an untouched form does not invalidate the index or mark drafts stale.

### 2.3 Evidence records and chunking

Code: `backend/app/services/indexing.py` (`build_evidence`). Route: `POST /api/profile/confirm`.

Confirmation is refused while a conflict is unresolved (409), when the profile is empty (422) or when it would produce more than `MAX_EVIDENCE_CHUNKS` records (422 `too_many_chunks`).

Evidence records are **semantic units, not arbitrary slices**. For every profile record:

| Unit | Content |
|---|---|
| Overview | For a skill group: the skill list. For any other record: its dates and location as confirmed. Created even when both are empty, because the record's existence is itself a fact. |
| Summary | The record's summary paragraph, if any |
| Statement | One per bullet |
| Skills | For a role, project, publication or other non-skill record that lists skills: one record with the text `[context] Skills: a, b, c` and category `skill`, whose parent is that record. It cites the line of the record's own text that names every skill (searched between the record's header and the next record's header); when no single line does, the excerpt is `Skills: ...` and the source is "not found". Without this unit, skills listed under a role were in the confirmed profile but in no evidence, so they could not be cited. |

A bullet or other statement of at most 250 words is one evidence record. Longer text is split by `chunk_text` at paragraph and sentence boundaries into chunks of roughly 100 to 250 words; a single sentence longer than 250 words is cut by word count as a last resort. A **summary** is cut finer: above 120 words it is split at single line breaks and sentence groups into records of about 40 to 120 words, each located in the source on its own, so a citation opens the supporting passage instead of a whole page of prose. Chunks do not overlap. Instead, **every** evidence text starts with a short context prefix naming its record, for example `[Software Engineer at Quillfeather Software] ...`, so a bullet still says which role it belongs to once it stands alone.

Each evidence document stores:

- `excerpt`: the wording shown when a citation is opened. For extracted text this is the slice of the original source (`source.start`/`source.end` are offsets into the pasted text). For the user's own statements it is their wording, with `source.source_type: "user"`. If the span was never located, `source_type` is `"unknown"`.
- `text`: the context prefix plus the statement with e-mail addresses, phone numbers and URLs removed. This is what gets embedded. A chunk that consists only of contact details produces no evidence.
- `tags`: the profile's skills that occur in the statement (whole-term match).
- `parent`: record ID, category, title and organisation of the record it belongs to.
- `content_hash`: SHA-256 of `text`; `embedding_model`, `embedding_dimension`, `embedding_status`.
- `owner_id`, `profile_id`, `profile_version`, `position`, `record_id`, `bullet_id`, `source_revision`, `provenance`, `created_at`, `expires_at`.

The `evidence_id` is deterministic: the first 32 hex characters of SHA-256 over owner, profile, version, position and content hash. Building the evidence of one version twice yields the same documents, so confirming is idempotent, a retry continues where the last attempt stopped, and two simultaneous confirms converge on the same set.

### 2.4 Embedding and cache

Code: `backend/app/services/indexing.py` (`_IndexRun`), `backend/app/repositories/evidence.py`.

1. Documents already stored for this version are kept. If any of them was embedded with a different model or dimension, the version's evidence is deleted and rebuilt; vectors of different models are never mixed.
2. **Cache:** before calling the provider, the server looks for any evidence document of the same owner with the same `content_hash`, `embedding_model` and `embedding_dimension` that is already embedded, and copies its vector. An unchanged statement is therefore embedded once, however often the profile is edited and confirmed again.
3. The remaining texts are embedded in batches of 50. After each batch the vectors are stored and `profile.index_progress` (`total`, `embedded`) is updated; the profile is `index_state: indexing` meanwhile. The frontend polls `GET /api/profile` and shows these real counts.
4. If a batch fails (provider error, or the global daily AI-call cap), the batch is marked `failed`, the profile becomes `index_state: failed` with a user-readable `index_error`, and the error is returned. Vectors stored so far are kept. Confirming again embeds only what is missing.
5. The profile is marked `confirmed` / `indexed` / `indexed_version = version` only after a database count shows every evidence document of the version embedded, and only if the profile version has not changed in the meantime.
6. **Pruning.** After that, the owner's evidence of every other profile version is deleted, except versions that one of the owner's stored drafts was generated from (`GenerationRepository.profile_versions`), whose citations must keep opening. It runs after the cache lookup of step 2, so unchanged statements still reuse their vectors. The `profile_indexed` log event reports `pruned_chunks`.

### 2.5 Job analysis

Code: `backend/app/services/job_service.py`. Routes: `POST /api/jobs`, `PATCH /api/jobs/{job_id}`.

The description is stored untouched; the model reads a whitespace-normalised copy and returns a role summary and requirements (text, category, importance, inferred, a verbatim quote, keywords). The server then:

- locates each quote in the description (`source_span`); a requirement without a locatable quote is marked `inferred`;
- keeps only keywords that occur in the requirement text or its quoted passage (at most 12);
- drops a requirement that is, or was quoted from, an instruction-like line; drops an instruction-like role summary;
- merges repeated requirements (if either mention is required, it is required);
- stores every requirement of category `responsibility` (a duty) as `inferred`, whatever the model said;
- caps the list at `MAX_REQUIREMENTS`, keeping required before preferred and stated before inferred, so duties are cut first.

The prompt asks for required qualifications first, then preferred ones, then only those duties that name a checkable skill, tool or method not already covered; statements about values, culture, benefits, pay and location are left out; and `role_summary` must name the technologies and problem areas the posting gives anywhere, because it is the role query used for retrieval.

The API lets a client edit, add or remove requirements (`PATCH /api/jobs/{job_id}`): an edited or added requirement is marked `user_edited`, and a change increases the job's `version`. The web app no longer uses that route. It shows the extracted requirements read-only in the workspace's Requirements tab; the user's say over them is the per-requirement "Correct" control in the Coverage tab.

### 2.6 Retrieval

Code: `backend/app/services/retrieval.py`.

**Queries.** One query per requirement (its text) plus one role query (the job title and the analysed role summary, or the start of the posting when there is no summary; at most 600 characters). Contact details are stripped from query texts. All queries are embedded in one batched provider call.

**Scope.** `PythonRetriever` loads evidence with a MongoDB query that filters on `owner_id`, `profile_id` and `profile_version`. Evidence of another visitor or another profile version is never loaded, so it cannot be returned. Every loaded document must be embedded with the model and dimension in use now; otherwise the request fails with `409 profile_not_indexed` rather than ranking mixed vectors.

**Ranking.** For each query, two rankings of the loaded evidence are computed:

- semantic: cosine similarity between the query vector and each evidence vector, `cos(q, r) = (q . r) / (|q| |r|)`. A zero vector gets similarity 0 instead of dividing by zero.
- keyword: the fraction of the query's distinct tokens (requirement text plus its keywords, stopwords removed) that occur in the evidence text and tags.

Items with a score of 0 or less are left out of a ranking; ties keep profile order. The two rankings are combined by **reciprocal rank fusion**:

```text
fused(item) = sum over the rankings that contain it of  1 / (60 + rank)      (rank 1 = best)
```

Only ranks are used, so the two scores never have to share a scale. The constant 60 is the value from the original RRF paper.

**Selection and bounds.**

| Step | Rule | Default |
|---|---|---|
| Pool per query | best `3 x RETRIEVAL_PER_REQUIREMENT` by fused score | 12 |
| Diversity | walk the pool; take a candidate unless its role or project already has 2 picks; fill remaining places with the skipped ones in rank order | |
| Kept per query | `RETRIEVAL_PER_REQUIREMENT` | 4 |
| Merge | round robin: every query's best, then **each employment record's best statement for the role query**, then every query's second, ...; skip duplicates by `evidence_id`; skip a record that would exceed the token budget | |
| Context size | at most `RETRIEVAL_MAX_CONTEXT` records | 18 |
| Token budget | at most `RETRIEVAL_TOKEN_BUDGET` estimated tokens (about 4 characters per token) | 6000 |

Keyword overlap compares singular forms on both sides ("APIs" matches "API"). The per-role statements are there so that each confirmed role has something the model can write a bullet from; with many requirements and a small context a role can still be absent, and then the server's fallback in [section 2.9](#29-one-bounded-correction-pass-then-omission) applies.

The selected context is returned in profile order. Its IDs are stored on the draft as `retrieved_evidence_ids` (followed by the evidence cited by fallback bullets), together with `profile_version`, for auditability. Scores are ordering aids only: they never leave the module and are not confidence values.

### 2.7 Generation with aliases and server-composed metadata

Code: `backend/app/services/generation.py` (`GenerationService`), `backend/app/services/drafting.py` (pure functions), prompt `backend/app/prompts/generation.md`.

**What the model sees** (`LLMGenerationContext`): the job's title, company and role summary; the requirements as `R1`, `R2`, ... with their retrieved candidates; the confirmed non-skill records as `P1`, `P2`, ... (category, title, organisation, location, dates) so bullets can be grouped; the applicant's name; the profile's skill list; and the retrieved evidence as `E1`, `E2`, ... Each evidence item is marked `statement` (something the person did) or `record_details` (only the header of a role, project or degree). The model **never sees a database ID** and can only cite aliases.

**What the model returns** (`LLMGeneration`): summary statements, bullets grouped under record aliases for experience and projects, skills, cover-letter paragraphs (each labelled factual or connective) and one coverage rating per requirement, all citing aliases.

**What the server composes itself** (`compose_draft`):

- Aliases are mapped back to IDs. An alias the server did not issue is dropped and counted; the draft then carries a warning with the number of ignored citations.
- **Contact details** come from the confirmed profile.
- **Experience** lists every confirmed employment record in profile order, with heading, organisation, location and date range copied verbatim from the record, even when the model wrote no bullets for it. The employment history is mandatory metadata, not a retrieval result. A role left without bullets is filled by the server from the confirmed profile ([section 2.9](#29-one-bounded-correction-pass-then-omission)).
- **Projects** holds the project, publication and achievement records the model chose to list, with confirmed headers. An entry that ends without bullets is dropped unless its confirmed record has neither bullets nor a summary.
- Within one entry, a supported bullet that repeats at least 75% of the words of the shorter of it and an earlier bullet is flagged `needs_review` ("This bullet repeats most of another bullet of the same entry."). Nothing is merged or deleted.
- A record's section follows its **confirmed category**, not where the model placed it, so a personal project cannot be presented as employment.
- **Education and certifications** are the confirmed profile text, unchanged, each line citing the evidence built from it.
- A bullet written under an unknown alias, or under a record that cannot hold bullets, goes to `omitted_claims`.

The model therefore cannot supply names, titles, dates, degrees or contact details at all.

### 2.8 Deterministic validation rules

Code: `backend/app/services/validation.py`, `check_statement` and `check_skill` in `backend/app/services/drafting.py`.

Every statement ends with one status and a list of human-readable warnings. The strictest finding decides the status.

| Status | Meaning |
|---|---|
| `supported` | every check passed against the evidence the statement cites |
| `needs_review` | something could not be confirmed; the statement stays, flagged |
| `unsupported` | a concrete problem was found |
| `not_applicable` | connective cover-letter text that makes no claim |
| `user_edited` | edited by the user and not yet revalidated |

The rules:

**(a) Evidence membership.** Only aliases issued for this draft count. For a bullet under a role or project, evidence belonging to a different record is dropped as well. A factual statement left with no valid evidence is `unsupported`.

**(b) Numbers, with unit and context.** Every figure in a statement (percentages, multipliers such as `3x`, currency amounts, counts such as `5,000+` or `5k`; number words from two to ninety-nine are read as digits) must occur in the cited evidence with the same value and unit. `5,000+` additionally needs the evidence to say `5,000+` or "over 5,000". Then the context is compared: the five nearest words on each side of the figure in the statement (stopwords excluded) must share at least one word stem with the three nearest content words on each side of that figure in the evidence, within the same sentence. Words that only say a figure went up or down ("reduced", "improved") are ignored. So "lowered cloud costs by 20%" is `unsupported` when the only 20% in the cited evidence is "improved unit test coverage by 20%". A figure with no describing words around it is accepted when the figure itself is in the evidence. In the cover letter, a figure that is part of the job's own title or company name ("SDE 2", "3M") is not treated as a figure. Two refinements: a figure written together with a common unit ("420ms", "1.4GB", "40k") is read as the figure plus the unit on both sides, so it matches "420 ms" in the evidence and an invented "90ms" is rejected as a figure; and what a figure measures excludes the method clause after "by", "through", "via", "using" or "with", so "reduced cloud costs by 20% by adding pytest suites" does not pass on the shared method when the evidence's 20% is about test coverage.

**(c) Names and requirement keywords.** A word is checked when it is written like a name (capitalised in mid-sentence, or spelled like a technology: `PostgreSQL`, `AWS`, `c++`, `node.js`) or when it is a keyword of the job's requirements. Such a term must occur in the cited evidence. If it does not: `needs_review` when it occurs elsewhere in the confirmed profile, `unsupported` when it occurs nowhere in the profile (Kubernetes, when the profile only mentions Docker). In the cover letter, the job's own title and company and the applicant's name may be used, with one exception: a word of the title or company that is also a requirement keyword the confirmed profile lacks is not unlocked (a "Kubernetes Platform Engineer" posting does not let the letter claim Kubernetes); the literal title and company can still be written out. Halves of compound descriptors from the posting ("front-end", "full-stack", "large-scale", "real-time", "hands-on") are not treated as required terms.

**(d) Expertise and seniority wording.** "expert", "extensive", "proficient", "advanced", "in-depth", "N years", and seniority words ("senior", "principal", "lead engineer", "tech lead") must be used literally by the cited evidence, which includes the record's own title; otherwise `needs_review`.

**(e) Skills.** A skill is listed only if at least one evidence record of the confirmed profile version contains every word of its name (this includes the `Skills:` record of a role or project), or if a confirmed record plainly lists it; the server chooses the citations itself. A skill the profile mentions only as coursework or limited exposure (a skill group titled "Coursework exposure only", wording such as "basic familiarity") is not offered to the model and is `needs_review` if it appears without that qualifier; the same applies to a summary or cover-letter sentence that drops the qualifier.

**(g) Evidence borrowed across records.** In the summary and the cover letter, a sentence that names an employer is validated again without the cited evidence of every role and project it does not name. If it no longer holds, the statement is `needs_review` ("This sentence names X but relies on evidence from another role or project."), so a personal project cannot be presented as work done at an employer.

**(f) Connective text.** A cover-letter paragraph with no evidence that the model labelled as connective is `not_applicable`, but the label is not trusted: a figure makes it `unsupported`; a name or a requirement keyword that is not in the profile, or expertise wording, makes it `needs_review`.

**Optional semantic verifier.** With `ENABLE_SEMANTIC_VERIFIER=true` (default off), statements the deterministic checks accepted are also sent to the model with their evidence. Its answer can only make a status stricter. If the call fails, the deterministic result stands and the draft gets a warning.

### 2.9 One bounded correction pass, then omission

Code: `GenerationService._generate`, `collect_feedback`, `remove_unsupported`.

If the first draft contains unsupported statements that can be rewritten (anything but skills), the model is called **once** more with the concrete findings (at most 20). A rejected skill alone does not trigger the pass: it is dropped and listed in `omitted_claims`. The second draft replaces the first only if it has no more unsupported statements than the first. There is no loop. If the correction call fails, the first draft is kept and the draft carries a warning.

Afterwards every statement that is still `unsupported` is **removed** from the documents and listed in `omitted_claims` with its section, text and reason. `needs_review` statements stay, flagged.

**No bare role headings** (`fill_empty_entries`). A bullet may only cite evidence of its own role, and retrieval selects by relevance to the job, so a confirmed role can end up with no bullets. Such a role gets up to two of its own confirmed bullets word for word (or its summary when it has no bullets), `supported`, citing the evidence built from them; they can be edited and regenerated like any bullet. Bullets written in the first person are skipped as remarks, and a record with nothing usable keeps its heading.

A whole generation is stopped after 270 seconds (`GENERATION_DEADLINE`), 30 seconds before a running record may be taken over by a retry, and fails as `504 provider_timeout`.

### 2.10 Coverage

Code: `backend/app/services/coverage.py`.

The model proposes a status per requirement and one sentence of explanation; the server checks it. The server can lower a rating and never raises one.

**What is compared.** Whole phrases as the posting writes them ("GitHub Actions", "CI/CD", "REST APIs"), never the single words they are made of. Names match exactly, ordinary words by stem, and an acronym in the evidence answers a spelled-out keyword ("ML" for "machine learning"). A short table of other common names (`ALIASES`: "Google Cloud" for "GCP", "Amazon Web Services" for "AWS", "Kubernetes" and "k8s", "PostgreSQL" and "Postgres", "JS", "TS") lets a rating stand when the evidence names the same thing differently; it never raises a rating. Alternatives form one group that a single mention answers: lists joined by "or" or "/", examples after "such as", "e.g." or "including", lists in brackets, and lists introduced by "at least one" or "one or more". Open-ended lists ("or a related field") demand nothing. Descriptor words ("front-end", "tools", "experience") and the hiring company's own name are not checked. In a duty (category `responsibility` or `other`) only job-analysis keywords are checked.

| Model's proposal | Server's result |
|---|---|
| none | `uncertain` |
| `missing` | `missing`, with fixed wording: no evidence was found in the supplied profile, which does not mean the person lacks it |
| `supported` or `partial` without valid evidence | `uncertain` |
| the requirement names a metric the cited evidence does not show for the same thing | `uncertain` |
| a qualification whose only checkable phrase is a job keyword that occurs nowhere in the whole confirmed profile | `missing`, no evidence, "No evidence of X was found in the supplied profile. ..." |
| the cited evidence answers fewer than half of the requirement's groups | `uncertain` |
| `supported`, at least half but not all groups answered | `partial` |
| `partial` for a duty or general statement with nothing checkable | `uncertain` |

**Rationale.** The model's sentence is shown unless it introduces a name, a job keyword, a figure or expertise wording found in neither the requirement, the cited evidence nor the job's title and company; then a server sentence replaces it. When the server lowers a rating it keeps the model's sentence and appends its reason, naming whole phrases in the posting's order ("... The cited evidence mentions Python and FastAPI but not REST APIs, so this is rated partial."). The model's sentence is dropped when it names the very phrase the evidence lacks.

The summary is computed by the server:

```text
assessed = supported + partial + missing            (uncertain is counted separately)
percent  = round(100 * (supported + 0.5 * partial) / assessed, 1)
percent  = null when assessed == 0                  (shown as "Unavailable", never as 0)
```

Weights are equal. The user can override any status with a note (`user_corrected: true`); the evidence and original rationale stay visible. **This number is evidence coverage. It is not a suitability score, an ATS score or a hiring probability.**

### 2.11 Editing, revalidation, single-item regeneration, staleness

- **Edit** (`PATCH /api/generations/{id}`): an edited statement becomes `user_edited` and loses its verdict; `validation.state` becomes `needs_revalidation`. No text is regenerated.
- **Revalidate** (`POST .../validate`): statements the user edited are checked again against the evidence they cite, using the evidence of the profile version the draft was generated from. Text is never changed, only statuses and warnings. An edited cover-letter paragraph that cites nothing stays `not_applicable` only when every sentence is a plain greeting, thanks or closing; otherwise it is `needs_review`, because it could not be checked.
- **Regenerate one item** (`POST .../items/{item_id}/regenerate`): only for summary, experience, project and cover-letter statements. The model works from the draft's stored `retrieved_evidence_ids`; the user's optional instruction travels as data. The result is validated and stored with its real status. Unlike initial generation, an `unsupported` result is **kept and flagged**, not removed.
- **Staleness** is computed on every read: a draft is stale when the profile or job it was generated from now has a different version (`stale_reasons`: `profile_changed`, `job_changed`). Evidence of the profile version a stored draft was generated from is kept (other old versions are pruned at the next confirm, [section 2.4](#24-embedding-and-cache)) so a stale draft can still show and validate its citations.

Drafts use optimistic locking: `revision` increases on every edit, validation and regeneration, and a write based on an old revision is refused with 409.

## 3. Why this is retrieval-augmented generation

The three parts of RAG are all present and are separate steps in the code:

1. **Index.** The confirmed profile is split into evidence records, each embedded and stored with its model, dimension and version ([2.3](#23-evidence-records-and-chunking), [2.4](#24-embedding-and-cache)).
2. **Retrieve.** For each job, queries are embedded and the evidence of one owner and one profile version is ranked; a bounded subset is selected ([2.6](#26-retrieval)).
3. **Generate from the retrieved context.** Only that subset, plus mandatory confirmed metadata, is given to the model, and every statement must cite it ([2.7](#27-generation-with-aliases-and-server-composed-metadata), [2.8](#28-deterministic-validation-rules)).

Where the ranking runs does not change this. Here it runs in the application process: all vectors of one profile version (at most `MAX_EVIDENCE_CHUNKS`, 200 by default) are loaded and compared exactly with numpy. For that size an exact scan is cheap and needs no vector index.

**MongoDB Atlas Vector Search is not used.** MongoDB stores the vectors as ordinary arrays and filters by owner, profile and version; it does no similarity search. `RETRIEVAL_MODE` accepts only `python`, and any other value stops startup with a clear message.

Atlas Vector Search is the documented upgrade path. Retrieval sits behind a small protocol:

```python
class Retriever(Protocol):
    async def retrieve(self, owner_id, profile_version, query, filters, limit) -> list[ScoredEvidence]: ...
```

A second implementation would have to apply the owner, profile and version filters inside the vector query (not after a cross-user search), use an index whose dimension matches the embedding model, and return the same results as `PythonRetriever` on the fixtures. None of that exists yet.

## 4. Data model

Code: `backend/app/schemas/documents.py` (stored shapes), `backend/app/db.py` (collections and indexes). Indexes are created idempotently at startup and nothing is ever dropped. IDs are 32 hex characters. Timestamps are stored as BSON dates in UTC and returned as ISO-8601 strings ending in `Z`.

| Collection | Main fields | Indexes |
|---|---|---|
| `sessions` | `_id` (session ID), `token_hash`, `owner_id`, `created_at`, `expires_at`, `revoked_at`, `quota` (attempts used per operation) | `token_hash` unique; TTL on `expires_at` |
| `sources` | `_id`, `owner_id`, `profile_id`, `label`, `source_type`, `text` (original, untouched), `revision`, `content_hash`, `char_count`, `position`, `created_at`, `expires_at` | `owner_id`; TTL |
| `profiles` | `_id` (profile ID), `owner_id`, `version`, `status`, `index_state`, `indexed_version`, `index_progress`, `index_error`, `contact`, `records[]`, `conflicts[]`, timestamps, `expires_at` | `owner_id` unique (one profile per session); TTL |
| `evidence` | `_id` (deterministic), `owner_id`, `profile_id`, `profile_version`, `position`, `record_id`, `bullet_id`, `source`, `source_revision`, `provenance`, `excerpt`, `text`, `category`, `tags`, `parent`, `embedding_model`, `embedding_dimension`, `content_hash`, `embedding`, `embedding_status`, `created_at`, `expires_at` | `(owner_id, profile_id, profile_version)`; `(owner_id, content_hash)` for the cache; TTL |
| `jobs` | `_id`, `owner_id`, `version`, `company`, `title`, `description`, `role_summary`, `requirements[]`, timestamps, `expires_at` | `(owner_id, created_at desc)`; TTL |
| `generations` | `_id`, `owner_id`, `idempotency_key`, `status`, `error`, `revision`, `attempt`, `started_at`, `job_id`, `job_version`, `job_title`, `company`, `profile_id`, `profile_version`, `provider_mode`, `model`, `retrieved_evidence_ids`, `resume`, `cover_letter`, `coverage`, `coverage_summary`, `validation`, `omitted_claims`, `warnings`, `usage`, `regeneration_keys`, timestamps, `expires_at` | `(owner_id, created_at desc)`; `(owner_id, idempotency_key)` unique; TTL |
| `rate_limits` | `_id` (counter key), `count`, `expires_at` | TTL |

**TTL.** Every collection has a TTL index on `expires_at` with `expireAfterSeconds=0`. Every owned document gets the session's expiry time, so all of a session's data disappears together. MongoDB's TTL task runs about once a minute, so the application also checks expiry itself on every request.

**Versions.** `profiles.version` and `jobs.version` are integers that increase on every change; `generations` records the versions it was generated from, which is how staleness is detected. Evidence is keyed by `profile_version`.

Vectors are returned by the repository only when a caller asks for them (`with_vectors=True`), and the API model for evidence has no vector field, so embeddings cannot leave the server.

The optional `applications` collection of the specification is not implemented.

## 5. Sessions: isolation, expiry and deletion

Code: `backend/app/security.py`, `backend/app/services/sessions.py`, `backend/app/repositories/base.py`.

**Token.** `POST /api/sessions` creates a token with `secrets.token_urlsafe(32)` (256 bits of randomness) and returns it once. Only its SHA-256 hash is stored. The browser keeps the token in `sessionStorage` under one key (`resume-tailor.session`) and sends it as `Authorization: Bearer ...`. It is never put in a URL, a cookie, `localStorage` or a log line. The server does not accept a token from the query string.

**Ownership.** Each session has a random `owner_id`, separate from the session ID and the token. `require_session` resolves the owner from the token on every protected request. No route accepts an owner ID from the client. Every repository method takes `owner_id` and puts it in the MongoDB filter, and refuses to store a document whose owner differs. A document ID alone therefore reaches nothing: a non-owned ID answers `404 not_found` exactly like a missing one.

**Expiry.** Sessions last `SESSION_TTL_HOURS` (24 by default). `expires_at` is compared with the current time on every request, so access ends at expiry even before the TTL task has deleted anything.

| Token state | Response |
|---|---|
| missing, malformed, unknown or revoked | `401 unauthorized` (a revoked token is still accepted by `DELETE /api/session` until it expires) |
| expired | `401 session_expired` |

**Clear my data** (`DELETE /api/session`):

1. The session is revoked first (`revoked_at` is set atomically). The token stops working at once.
2. The owner's documents are deleted from `generations`, `jobs`, `evidence`, `profiles` and `sources`, derived data first.
3. The revoked session document stays as a tombstone until its TTL, so requests already in flight can still see the revocation.
4. The call is safe to repeat. If the delete pass fails part-way with a database error (503), the token is already revoked; `require_session_to_clear` lets this one route accept it again, and the second pass removes what the first left. Every other route keeps answering 401.

**The in-flight guard.** A long operation can spend a minute in a provider call. If the user clears their data during that time, the operation would store its result after the deletion ran. To prevent that, every long operation calls `SessionService.guard_after_write` right after its last write. The guard reads the session again; if it was revoked or has expired, it deletes the owner's documents once more and ends the request with 401. This is correct for either ordering: if the revocation came before the guard's check, the guard deletes; if it comes after, the deletion pass of "Clear my data" runs after the write and removes it.

The guard is called by ingest and confirm (in a `finally` block, so also after a failure), job analysis, generation (after storing the result and after recording a failure), and every change to a draft (edit, validate, regenerate). `PATCH /api/profile` and `PATCH /api/jobs/{id}` are single conditional writes with no provider call; they cannot recreate a deleted document.

**Concurrency.** Profile and job edits carry `expected_version`; draft edits carry `expected_revision`. A generation is claimed by inserting a `running` document under the unique `(owner_id, idempotency_key)` index, so two simultaneous requests with one key cannot both run. A `running` record older than five minutes counts as abandoned and may be restarted by a retry with the same key; a result arriving from the abandoned attempt is discarded.

## 6. AI providers, the fake provider and demo mode

Code: `backend/app/providers/`.

Application code depends only on the `AIProvider` protocol (`base.py`): `embed`, `extract_profile`, `analyze_job`, `generate_documents`, `regenerate_item`, `verify_claims`. Each call returns its result with the token usage it consumed, or raises a `ProviderError` subclass with a message that is safe to show to users. `providers/factory.py` is the only place that reads `AI_PROVIDER`.

**OpenAI provider.** `providers/openai/client.py` is the only module that calls the SDK. Every generation call:

- uses the Responses API with a Pydantic model as strict JSON schema, so output that does not match the schema is rejected. LLM-facing models (`LLMModel`) forbid extra fields and declare every field as required;
- sends the trusted prompt in `instructions` and the untrusted material as one JSON document in `input`; the two are never concatenated;
- sets `store=False` and `max_output_tokens` per operation, and sends `reasoning.effort` only when `OPENAI_REASONING_EFFORT` is non-empty. `temperature` and `top_p` are never sent because the configured models reject them;
- runs under an overall time limit (`PROVIDER_TIMEOUT_SECONDS`, retries included) with at most `PROVIDER_MAX_RETRIES` SDK retries;
- logs model, token counts and duration, never prompt or response text.

A refusal, a truncated response or a response that does not parse becomes `502 provider_invalid_output`. An embedding of the wrong size is rejected before it can be stored. Configuration validation requires `OPENAI_EMBEDDING_DIMENSIONS` to match the known size of the embedding model.

**Fake provider.** `providers/fake/` is deterministic and rule-based: extraction reads common resume layouts line by line, job analysis reads headings and bullets, generation only reuses evidence text (at most shortened to whole sentences) and cites it, and embeddings are hashed bag-of-words vectors of 256 dimensions (model name `fake-embedding-256`). It keeps call counters and can be told to fail or to wait, which the tests use.

It is built only when `AI_PROVIDER=fake` is set explicitly, and configuration validation rejects it when `APP_ENV=production`. It is **not a fallback**: a failing real provider call is reported as an error and is never replaced by fake output.

**Demo mode.** The API reports `provider_mode` (`openai` or `fake`) in `/readyz`, in session responses and on every draft. When it is `fake`, the frontend shows a persistent "Demo mode" banner and the privacy notice says that text is not sent to an AI provider.

## 7. Untrusted input

Resume, LinkedIn, notes and job text are all treated as untrusted. The defences are layered, and the last one does not depend on the model behaving:

1. **Separation.** User text only ever appears inside the JSON `input` of a provider call. Prompt templates (`backend/app/prompts/*.md`) contain no user text; nothing is interpolated into them. JSON encoding escapes quotes and line breaks, so text in a source cannot end its string and pose as another field.
2. **Instruction.** `prompts/untrusted_data.md` is prepended to every task prompt and states that the supplied data may contain instructions that must be treated as content.
3. **Heuristic guard.** `looks_like_instruction` recognises well-known phrasing (a `SYSTEM:` or `ASSISTANT:` line prefix, "ignore all previous instructions", "instructions for any AI"). A statement or requirement quoted from such a line is left out, visibly, with a note. This is a heuristic: it can miss a new wording.
4. **Grounding validation.** Whatever the model writes, a statement survives only if its figures, names and keywords are in the evidence it cites ([2.8](#28-deterministic-validation-rules)); coverage ratings and rationales are checked the same way ([2.10](#210-coverage)); citations outside the retrieved context, including another session's IDs, are dropped.
5. **Output handling.** All text is returned as JSON strings. The frontend renders it as text nodes and never uses `dangerouslySetInnerHTML`. Every response carries `X-Content-Type-Options: nosniff`.

## 8. Limits, quotas and cost controls

Code: `backend/app/config.py`, `backend/app/ratelimit.py`. All values are settings; defaults are shown.

**Input limits**

| Setting | Default | Enforced as |
|---|---|---|
| `MAX_PROFILE_CHARS` | 60,000 | 413 `input_too_large` on ingest (total over all sources) |
| `MAX_SOURCES` | 5 | 422 on ingest |
| `MAX_JOB_CHARS` | 25,000 | 413 on job creation |
| `MAX_REQUIREMENTS` | 25 | cap on analysis; 422 on job edit |
| `MAX_EVIDENCE_CHUNKS` | 200 | 422 `too_many_chunks` on confirm |
| `MAX_REQUEST_BYTES` | 400,000 | 413 for any request body, checked before it is parsed |

**Provider limits:** `PROVIDER_TIMEOUT_SECONDS` 180 (raised from 120 after a 47 s extraction of an 18,000-character profile was measured; the public deployment also lowers `MAX_PROFILE_CHARS` to 30,000 and `PROVIDER_MAX_RETRIES` to 1 in `render.yaml`), `PROVIDER_MAX_RETRIES` 2, `MAX_OUTPUT_TOKENS_EXTRACTION` 32,000, `_JOB_ANALYSIS` 8,000, `_GENERATION` 16,000, `_REGENERATION` 4,000, `_VERIFICATION` 6,000.

**Three persistent counters**, all stored in MongoDB so they survive restarts:

| Limit | Default | Scope | Error |
|---|---|---|---|
| `SESSION_CREATE_LIMIT_PER_HOUR` | 20 | per client IP, fixed one-hour window. The IP is stored only as a keyed hash (HMAC-SHA256). | 429 `rate_limited` with `Retry-After` |
| `QUOTA_INGEST` / `QUOTA_CONFIRM` / `QUOTA_JOB_ANALYSIS` / `QUOTA_GENERATION` / `QUOTA_REGENERATION` / `QUOTA_VALIDATION` | 8 / 15 / 15 / 12 / 40 / 40 | per session, for its whole lifetime, counted on the session document in one atomic update | 429 `quota_exceeded` with `details.operation` and `details.limit` |
| `GLOBAL_DAILY_AI_CALL_LIMIT` | 600 | all sessions together, per UTC day, counted before each provider call | 429 `quota_exceeded` with `details.scope: "global_daily"` |

**Which address is the client's** (`ratelimit.client_address`). With `TRUST_PROXY_HEADERS=false` (default) it is the socket peer and every header is ignored. With `true` the order is: the header named by `CLIENT_IP_HEADER` (blank by default; `cf-connecting-ip` on Render), `cf-connecting-ip`, `true-client-ip`, then `X-Forwarded-For` counted **from the right** with `TRUSTED_PROXY_HOPS` (default 1, range 1 to 10), then the socket peer. The leftmost `X-Forwarded-For` entry is written by the client and is never used; a header shorter than the hop count is ignored. `POST /api/sessions` logs `session_create` with `client_ip_source` (never the address).

Quota is **charged on attempt**, before the provider is called, so a request that later fails still counts. Failed provider calls cost money too, and this stops unbounded retries of a failing request. A replay of a completed generation (same `Idempotency-Key`) is answered from storage and charges nothing.

Other cost controls: the embedding cache, one batched embedding call per generation, one correction pass at most, the bounded context, and `store=False`.

## 9. Errors, logging and middleware

**Error envelope.** Every non-2xx response has the shape `{"error": {"code", "message", "request_id", "field_errors"?, "retryable"?, "details"?}}` (`backend/app/errors.py`). Messages are written for end users and contain no stack traces, provider payloads or secrets. The codes are listed in [API.md](API.md).

**Middleware** (`backend/app/middleware.py`, order outermost first): request context, CORS, unhandled-error boundary, body size limit. All are plain ASGI callables rather than Starlette's `BaseHTTPMiddleware`, so a client that disconnects does not cancel a provider call or a database write half way through.

- CORS allows only the exact origins in `CORS_ORIGINS` (a wildcard is rejected at startup), the methods GET, POST, PATCH and DELETE, and the headers `Authorization`, `Content-Type` and `Idempotency-Key`. Credentials are off, because the token travels in a header, not a cookie.
- Every response carries `X-Request-ID` and `X-Content-Type-Options: nosniff`; every `/api/` response carries `Cache-Control: no-store`.

**Logging** (`backend/app/logging_config.py`). One JSON object per line: timestamp, level, logger, message, request ID and structured fields. The access line has method, **route template** (for example `/api/jobs/{job_id}`, never the raw URL), status and duration. Provider lines have operation, model, token counts and duration. Request and response bodies, tokens, connection strings, source text and generated documents are never passed to a logger. A redaction filter additionally masks bearer tokens, `sk-` keys and MongoDB URIs. Library loggers that print request URLs (`httpx`, `httpx2`, `httpcore`, `httpcore2`, `openai`, `pymongo`) are limited to WARNING; the OpenAI SDK in use sends its requests through `httpx2`. In production, an unexpected exception is logged with its type and code location only, because exception messages can quote user input.

**Startup.** Configuration is validated when the app is created; an invalid value stops startup with a message that names the setting and never its value. Production requires `AI_PROVIDER=openai`, an API key, an explicitly set `CORS_ORIGINS` and an explicitly set, non-blank `MONGODB_URI`. An `OPENAI_API_KEY` or `IP_HASH_SALT` that is blank or still the `.env.example` placeholder (starts with `replace-with`) counts as not set, so `/readyz` reports the provider as `not_configured`. `/readyz` also returns the configured `limits`, the same object as the session responses. An unreachable database is **not** fatal: `/healthz` answers 200, `/readyz` answers 503, and index creation is retried on the next readiness check or the next request that needs the database.

## 10. Frontend

Code: `frontend/src/`.

- **Routing:** `BrowserRouter` with `/`, `/profile`, `/job`, `/workspace/:generationId` and a not-found route, all inside one shell (`components/app/AppShell.tsx`). The static host must rewrite every path to `index.html` for deep links to survive a refresh.
- **API access:** `lib/api.ts` is the only module that calls `fetch`. It returns typed data or throws an `ApiError` built from the error envelope. `VITE_API_BASE_URL` is the only build-time setting and is public.
- **Session:** `lib/sessionStore.ts` keeps `{token, expiresAt}` in `sessionStorage`. Any 401 clears the token, and the shell replaces the page with "Your session has ended". The session is created when the Start form is submitted, not on page load. Because a sleeping free-tier server can take about a minute to wake, session creation retries for up to about 90 seconds and tells the user the server is waking.
- **Server state:** TanStack Query. Reads retry at most twice and only for errors the API marked retryable. Mutations never retry automatically, because most are quota-charged AI calls; the user presses Retry, and input is kept after a failure.
- **Target job, one run:** `pages/job/JobPage.tsx` owns the run. `JobForm.tsx` does step 1 (`POST /api/jobs`); on success the page moves to `/job?job=<id>` (`ExistingJob.tsx`) and starts step 2 (`useGenerateDraft.ts`, `generateDraft.ts`), then opens the workspace. `RunProgress.tsx` lists both steps with a text status (Waiting, In progress, Done, Failed) in an `aria-live` region; there is no percentage. If step 1 fails the form keeps its input and Retry repeats the whole run. If step 2 fails the stored job stays on screen and Retry repeats only the generation, so the job is not analyzed (and charged) twice. The submit button is disabled while the profile is not confirmed and indexed.
- **Generation:** one idempotency key per attempt; pressing Retry sends the same key. If the first request did complete (for example its response was lost on the way back), the stored draft is returned instead of a second one being generated; if it failed, the same draft ID is run again. If the server answers `generation_in_progress`, the client polls the running draft every 3 seconds.
- **Workspace:** two panes from the `lg` width up (documents left; Evidence, Coverage and Requirements tabs right); on narrow screens Coverage and Requirements are tabs next to the two documents and evidence opens in a sheet. The Requirements tab (`RequirementsPanel.tsx`) loads the draft's job and lists its requirements read-only, grouped Required and Preferred. Statuses are shown with text labels, not colour alone.
- **Download PDF:** `pages/workspace/pdfDocument.ts` lays the active document out with jsPDF text calls (US Letter, 0.75 inch margins, built-in Helvetica and Times, selectable text) and saves it as `<Name> - Resume.pdf` or `<Name> - Cover Letter.pdf`. It reuses the section model of `workspaceModel.ts`, replaces characters the built-in fonts cannot draw (`pdfSafeText`), never splits a line across pages and keeps an entry heading with its first bullet. The module, and jsPDF with it, is loaded on the first download, not with the page. The file has no badges, statuses or controls.
- **Browser print:** there is no Print button. For a user who prints from the browser (Ctrl/Cmd+P), `pages/workspace/workspace.css` still prints exactly one document (the active one) as a single column of black text, without badges, controls or panels, with page-break rules for headings and entries; while flagged statements are unacknowledged it prints a notice instead.
- **Export gate:** Download PDF and Copy go through the same check (`pages/workspace/Workspace.tsx`, `ExportGateDialog.tsx`). If any statement is `needs_review`, `unsupported` or `user_edited`, a dialog lists those statements, offers to go to each one, and exports only after an explicit acknowledgement.
- **Sample data:** `src/sample/sampleData.ts` is generated from the fictional fixtures by `backend/fixtures/build_frontend_sample.py`. No real profile data is in the bundle.
- **Theme:** light and dark, stored in `localStorage` (the only thing kept there).

## 11. Known limitations

Stated plainly, because several of them affect how much the output can be trusted.

**Grounding**

- **Automated validation reduces fabrication; it cannot eliminate it.** The checks compare words, figures and names. A sentence can reuse the right words and still say something the evidence does not. Every draft must be read by the person whose name is on it.
- The numeric context check is a comparison of nearby words. A claim that shares one content word with the words next to the evidence figure passes, for example "cut test costs by 20%" against "unit test coverage by 20%".
- A capitalised name at the start of a sentence is only checked when it is also a keyword of the job's requirements, because it cannot be told apart from an ordinary first word.
- Connective cover-letter text that names something absent from the profile is kept and flagged `needs_review`, not removed.
- A statement regenerated on request, or edited and revalidated, is stored with its real status, including `unsupported`. Only initial generation removes unsupported statements.
- In the user interface, downloading or copying a draft with flagged statements requires an explicit acknowledgement but is not blocked, even when a statement is `unsupported`.
- The instruction guard is a list of known phrasings, not a general detector.
- The semantic verifier is off by default and is itself a language model; its answer is a second opinion, not a guarantee.

**Coverage**

- Coverage measures whether evidence for a requirement was found in the supplied text. It is **not** a suitability score, an ATS compatibility score or a hiring probability, and "missing" does not mean the person lacks the skill.
- All requirements have equal weight.
- The server's coverage check compares phrases, not meaning. Known gaps: a single-letter language name ("C", "R") is not checkable; an acronym in the requirement is not matched against a spelled-out phrase in the evidence (only the other direction); "A/B" is read as two alternatives; "N+ years" is judged by the model only, not compared with employment dates.

**Sessions and data**

- Sessions are anonymous and expire after 24 hours by default; there are no accounts and no recovery. The token lives in one browser tab's `sessionStorage`, so closing the tab loses access even before expiry.
- One profile per session. Input is pasted text only: no file upload, no URL fetching, no LinkedIn scraping.
- Evidence of earlier profile versions is pruned at each successful confirm, except versions a stored draft was generated from; those stay until the session expires.
- "Not captured" notices on a profile are recomputed only when the sources are ingested again, not when the user adds the text by hand.
- A bullet repeated word for word in two sources records only the first source as its provenance.
- Review flags on profile items do not block confirmation; only unresolved conflicts do.

**Operation**

- **Generation is synchronous.** The HTTP request stays open while the server retrieves, generates and validates (up to 270 seconds). There is no job queue, no background worker and no streaming; the interface shows "Generating..." without a percentage. If the process restarts mid-generation, the record stays `running` for up to five minutes before the same key can be retried.
- **Free-tier cold starts.** On Render's free plan the API sleeps after about 15 minutes without traffic and takes about a minute to wake. Only session creation waits for the wake-up automatically.
- Retrieval is an exact scan bounded to 200 evidence chunks per profile. It does not use a vector index and will not scale beyond small profiles without the upgrade described in [section 3](#3-why-this-is-retrieval-augmented-generation).
- Quota is charged before a generation is claimed, so two truly simultaneous requests with one key are both charged although only one runs.
- With `TRUST_PROXY_HEADERS=true` the client address comes from `CLIENT_IP_HEADER`, `cf-connecting-ip` or `true-client-ip` before `X-Forwarded-For`. On a platform that does not set or overwrite those headers a client could forge them, so the setting must only be enabled behind a proxy that does. Which source the deployed service actually uses is visible in the `session_create` log line (`client_ip_source`); this has **not** been checked on the deployed service after the change.
- `/docs`, `/redoc` and `/openapi.json` are served in every environment.
- A CORS preflight from a disallowed origin is answered by the framework with a plain-text 400, not the JSON error envelope.

**Scope**

- None of the optional features of the specification (section 7) is implemented: no PDF or DOCX upload, document library, application tracker, interview preparation, comparison view, DOCX export, Atlas Vector Search or accounts.
- What was and was not verified against the real OpenAI models and on the deployed service is recorded in [TESTING.md](TESTING.md) and [DEPLOYMENT.md](DEPLOYMENT.md).
