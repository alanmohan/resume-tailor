> AI-generated documentation (Claude Code, 2026-10-07). Reviewed and owned by the repository author.

# Resume Tailor: HTTP API

This is the API as implemented in `backend/app/api/` and `backend/app/schemas/` on 2026-10-07. The running server also serves an interactive description generated from the same code at `/docs` (Swagger UI), `/redoc` and `/openapi.json`; those list the success shapes and schema errors but not the application error codes, which are documented here.

- [Conventions](#conventions)
- [Errors](#errors)
- [Health](#health)
- [Sessions](#sessions)
- [Profile](#profile)
- [Evidence](#evidence)
- [Jobs](#jobs)
- [Generations](#generations)
- [Shared shapes](#shared-shapes)

## Conventions

| Topic | Rule |
|---|---|
| Format | JSON in and out, `snake_case` keys |
| Timestamps | ISO-8601 UTC with milliseconds and a `Z` suffix, for example `2026-10-07T21:30:00.000Z` |
| IDs | 32 lower-case hex characters. They are opaque and are not authorisation: every lookup is scoped to the caller. |
| Authentication | Every `/api` route except `POST /api/sessions` requires `Authorization: Bearer <token>`. The token is never accepted from the query string or a cookie. |
| Ownership | Resolved from the token on every request. No request field names an owner. A missing ID and another session's ID both answer `404 not_found`. |
| Text | All text fields are plain strings and may contain markup characters. Clients must render them as text. |
| Optimistic locking | Profile and job edits send `expected_version`; draft edits send `expected_revision`. A mismatch is `409 version_conflict`. |
| Request size | A body larger than `MAX_REQUEST_BYTES` (400,000 by default) is refused with `413 input_too_large` before it is parsed. |

**Response headers.** Every response carries `X-Request-ID` (also in the error body) and `X-Content-Type-Options: nosniff`. Every `/api/` response carries `Cache-Control: no-store`.

**CORS.** Only the exact origins listed in `CORS_ORIGINS` are allowed. Allowed methods: GET, POST, PATCH, DELETE. Allowed request headers: `Authorization`, `Content-Type`, `Idempotency-Key`. Exposed response headers: `X-Request-ID`, `Retry-After`. Credentials are disabled. Preflight results may be cached for 600 seconds. A preflight from an origin that is not allowed is answered with `400` and a plain-text body by the framework.

## Errors

Every non-2xx response from an application route, and every 404, 405, 422 and 500 anywhere, has this body:

```json
{
  "error": {
    "code": "version_conflict",
    "message": "This item was changed elsewhere. Reload it and try again.",
    "request_id": "0f3c...",
    "field_errors": [{"field": "sources.0.label", "message": "..."}],
    "retryable": true,
    "details": {"current_version": 4}
  }
}
```

`field_errors`, `retryable` and `details` are present only when they apply. `field` is the path inside the request body (`sources.0.label`, `records.2.bullets.0.bullet_id`); a header is reported as `header.<name>`; a body that is not valid JSON is reported as `body`. Rejected input is never echoed back.

| Code | Status | When | Extra fields |
|---|---|---|---|
| `validation_error` | 422 | Schema violation or an application-level input rule | `field_errors` (usually) |
| `too_many_chunks` | 422 | The profile would produce more evidence chunks than `MAX_EVIDENCE_CHUNKS` | `details.chunks`, `details.limit` |
| `idempotency_key_required` | 400 | `Idempotency-Key` missing or not 8 to 128 visible ASCII characters without spaces | |
| `unauthorized` | 401 | Token missing, malformed, unknown or revoked | |
| `session_expired` | 401 | Token valid but past `expires_at` | |
| `not_found` | 404 | Unknown route, or an ID that is missing or not owned | |
| `method_not_allowed` | 405 | Method not supported by the route | |
| `version_conflict` | 409 | `expected_version` / `expected_revision` is out of date | `details.current_version` (profile, job) or `details.current_revision` (draft) when the mismatch is detected before writing; absent when a concurrent write won the race |
| `unresolved_conflicts` | 409 | Confirming a profile that still has unresolved conflicts | `details.unresolved_conflict_count` |
| `profile_not_confirmed` | 409 | Generating without a confirmed profile | |
| `profile_not_indexed` | 409 | The confirmed version is not completely indexed with the current embedding model | |
| `generation_in_progress` | 409 | The same `Idempotency-Key` is still running, or the draft being changed is still running | `details.generation_id` |
| `input_too_large` | 413 | Profile text, job description or request body over its limit; the message names both sizes | `field_errors` for profile and job text |
| `rate_limited` | 429 | Too many sessions created from one client address in the current hour | `Retry-After` header (seconds) |
| `quota_exceeded` | 429 | A per-session quota or the global daily AI-call cap is used up | `details.operation` and `details.limit`, or `details.scope: "global_daily"` |
| `provider_timeout` | 504 | The AI provider, or a whole generation (270 s), took too long | `retryable` |
| `provider_rate_limited` | 503 | The AI provider is rate limiting or out of quota | `retryable` |
| `provider_unavailable` | 502 | The AI provider could not be reached or rejected the call | `retryable` (false when no key is configured, credentials are rejected or the request itself is rejected) |
| `provider_invalid_output` | 502 | Refusal, truncated or unparseable provider output | `retryable` |
| `database_unavailable` | 503 | MongoDB cannot be reached | `retryable: true` |
| `internal_error` | 500 | Unexpected failure; no detail is exposed | |

Errors that any authenticated route can return are not repeated below: `401 unauthorized`, `401 session_expired`, `413 input_too_large` (body size), `422 validation_error` (schema), `503 database_unavailable`, `500 internal_error`.

After a provider failure nothing that was already stored is lost: the previous profile, job or draft is unchanged and the same request can be sent again.

## Health

Neither route needs a token. Neither is under `/api`.

### `GET /healthz`

Liveness. Touches neither configuration nor the database.

`200` `{"status": "ok"}`

### `GET /readyz`

Readiness: the database answers a ping and has its indexes, and the AI provider is configured. No provider call is made.

`200` when ready, `503` otherwise, with the same body shape:

```json
{
  "status": "ready",
  "checks": {"database": "ok", "provider": "configured"},
  "provider_mode": "openai"
}
```

`status` is `ready` or `not_ready`; `checks.database` is `ok` or `unavailable`; `checks.provider` is `configured` or `not_configured`; `provider_mode` is `openai` or `fake`.

## Sessions

### `POST /api/sessions`

Creates an anonymous session. No token, no body. Rate limited per client address (`SESSION_CREATE_LIMIT_PER_HOUR`).

`201`

```json
{
  "token": "<43 URL-safe characters, shown once>",
  "expires_at": "2026-10-08T21:30:00.000Z",
  "provider_mode": "openai",
  "limits": {
    "max_profile_chars": 60000,
    "max_job_chars": 25000,
    "max_sources": 5,
    "max_requirements": 25,
    "session_ttl_hours": 24
  }
}
```

The server stores only a hash of the token; it cannot be shown again.

Errors: `429 rate_limited` (with `Retry-After`).

### `GET /api/session`

`200` `{"expires_at": iso, "provider_mode": "openai" | "fake", "limits": Limits, "has_profile": bool}`

### `DELETE /api/session`

"Clear my data". Revokes the token, then deletes every document the session owns.

`200`

```json
{"deleted": true, "deleted_counts": {"sources": 3, "profiles": 1, "evidence": 43, "jobs": 1, "generations": 1}}
```

Afterwards the token answers `401 unauthorized` on every route. An operation that was still running when the data was cleared ends with 401 and leaves nothing behind.

## Profile

One profile per session. See [Profile](#profile-1) under shared shapes.

### `POST /api/profiles/ingest`

Stores the labelled source texts and returns the extracted draft. One AI call; counts against `QUOTA_INGEST`.

Request:

```json
{
  "sources": [
    {"label": "Resume", "source_type": "resume", "text": "..."}
  ]
}
```

| Field | Rule |
|---|---|
| `sources` | 1 to `MAX_SOURCES` entries |
| `label` | 1 to 80 characters after trimming |
| `source_type` | `resume`, `linkedin` or `notes` |
| `text` | not blank; stored exactly as sent. The total length of all texts may not exceed `MAX_PROFILE_CHARS`. |

`200` [Profile](#profile-1) with `status: "draft"`. Ingesting again replaces the sources and the draft, keeps `profile_id` and increases `version`.

Errors: `422 validation_error` (blank text, bad label or type, too many sources), `413 input_too_large`, `429 quota_exceeded`, `409 version_conflict` (the profile was edited while extraction was running), provider errors.

### `GET /api/profile`

`200` [Profile](#profile-1), or `404 not_found` when the session has no profile yet.

### `PATCH /api/profile`

Saves the reviewed profile. No AI call. The body carries the **whole** list of records: a stored record that is left out is deleted.

```json
{
  "expected_version": 3,
  "contact": {"name": "...", "email": "...", "phone": null, "location": null, "links": []},
  "records": [
    {
      "record_id": "<existing id, or null for a new record>",
      "category": "employment",
      "title": "...",
      "organization": "...",
      "location": null,
      "start_date": "Jul 2022",
      "end_date": "Jul 2024",
      "summary": null,
      "bullets": [{"bullet_id": "<existing id or null>", "text": "..."}],
      "skills": []
    }
  ],
  "conflict_resolutions": [
    {"conflict_id": "...", "resolution": "resolved", "note": "The resume date is right."}
  ]
}
```

| Field | Rule |
|---|---|
| `record_id`, `bullet_id` | Echo the stored ID unchanged, or send `null` for an item you added. An unknown or repeated ID is `422` with the field path. |
| `category` | `employment`, `education`, `project`, `publication`, `achievement`, `skill`, `certification` |
| `title` | 1 to 300 characters |
| `organization`, `location`, contact fields | up to 300 characters; blank becomes `null` |
| `start_date`, `end_date` | up to 80 characters, free text, kept exactly as written |
| `summary` | up to 6,000 characters |
| `bullets[].text` | 1 to 4,000 characters; at most 80 bullets per record |
| `skills[]` | 1 to 120 characters each; at most 300 per record |
| `contact.links[]` | up to 400 characters each; at most 20 |
| `records` | at most 300 |
| `conflict_resolutions` | optional; `resolution` is `resolved` or `dismissed`; `note` up to 500 characters |

`200` [Profile](#profile-1).

- Something changed: `version` + 1, `status: "draft"`, `index_state: "not_indexed"`, `indexed_version: null`. The profile must be confirmed again before generating.
- Nothing changed: the stored profile at the same version.
- Changed text becomes `provenance: "user_edited"`; a new item becomes `"user_added"`.

Errors: `404 not_found` (no profile), `409 version_conflict`, `422 validation_error`.

### `POST /api/profile/confirm`

Confirms the reviewed profile and builds its evidence index. Counts against `QUOTA_CONFIRM`; each embedding batch counts as one AI call.

Request: `{"expected_version": 4}`

`200` [Profile](#profile-1) with `status: "confirmed"`, `index_state: "indexed"`, `indexed_version == version`.

While the request runs, `GET /api/profile` reports `index_state: "indexing"` and real counts in `index_progress`.

Errors:

| Error | Meaning |
|---|---|
| `404 not_found` | no profile |
| `409 version_conflict` | the version is out of date, or the profile changed while it was being indexed |
| `409 unresolved_conflicts` | resolve or dismiss every conflict first |
| `422 validation_error` | the profile is empty (`records`) |
| `422 too_many_chunks` | shorten the profile |
| `429 quota_exceeded` | confirm quota or global daily cap |
| `409 profile_not_indexed` | indexing ended without every chunk embedded |
| provider errors | the profile is left at `index_state: "failed"` with `index_error` set |

Confirming again after a failure is safe: chunks that already have a vector are not embedded again.

## Evidence

### `GET /api/evidence/{evidence_id}`

The source text behind a citation. Works for every `evidence_id` that appears in one of the caller's drafts, including evidence of an older profile version cited by a stale draft.

`200`

```json
{
  "evidence_id": "...",
  "excerpt": "Reduced median API response time from 420 ms to 290 ms ...",
  "text": "[Software Engineer at Quillfeather Software] Reduced median API response time ...",
  "category": "employment",
  "source": {"source_id": "...", "label": "Resume", "source_type": "resume", "start": 812, "end": 913},
  "provenance": "extracted",
  "parent": {"record_id": "...", "category": "employment", "title": "Software Engineer", "organization": "Quillfeather Software"},
  "tags": ["redis", "postgresql"],
  "profile_version": 4
}
```

| Field | Meaning |
|---|---|
| `excerpt` | The wording to show. For extracted text it is the slice `start`..`end` of the original pasted source. |
| `text` | The indexed form: a `[record context]` prefix plus the statement with contact details removed |
| `source.source_type` | `resume`, `linkedin`, `notes`; `user` for a statement the user wrote or edited during review (`source_id`, `start`, `end` are `null`); `unknown` when the source span was not located |
| `provenance` | `extracted`, `user_edited` or `user_added` |
| `parent` | The role, project or other record the evidence belongs to |

Embedding vectors are never returned.

Errors: `404 not_found`.

## Jobs

### `POST /api/jobs`

Analyses a job description into editable requirements. One AI call; counts against `QUOTA_JOB_ANALYSIS`.

Request: `{"description": "...", "company": "Fernhollow AI", "title": "Applied Machine Learning Engineer"}`

`description` must not be blank and may not exceed `MAX_JOB_CHARS`; it is stored exactly as sent. `company` and `title` are optional (up to 200 characters; blank becomes `null`).

`201` [Job](#job) at `version: 1`.

Errors: `422 validation_error`, `413 input_too_large`, `429 quota_exceeded`, provider errors.

### `GET /api/jobs`

`200` `{"jobs": [{"job_id", "title", "company", "version", "requirement_count", "created_at", "updated_at"}]}`, newest first, at most 100.

### `GET /api/jobs/{job_id}`

`200` [Job](#job), or `404 not_found`.

### `PATCH /api/jobs/{job_id}`

Saves the reviewed requirements. No AI call. The body carries the **whole** list.

```json
{
  "expected_version": 1,
  "company": "Fernhollow AI",
  "title": "Applied Machine Learning Engineer",
  "requirements": [
    {"requirement_id": "<existing id or null>", "text": "...", "category": "skill", "importance": "required"}
  ]
}
```

| Field | Rule |
|---|---|
| `requirements` | at most `MAX_REQUIREMENTS` |
| `requirement_id` | echo the stored ID, or `null` for a requirement you added; unknown or repeated is `422` |
| `text` | 1 to 500 characters |
| `category` | `skill`, `experience`, `education`, `certification`, `responsibility`, `other` |
| `importance` | `required` or `preferred` |
| `company`, `title` | if the key is absent the stored value is kept; an explicit `null` clears it |

`200` [Job](#job): `version` + 1 when something changed, the same version otherwise. A changed or added requirement gets `user_edited: true`.

Errors: `404 not_found`, `409 version_conflict`, `422 validation_error`.

## Generations

A generation is one draft: a resume, a cover letter and a coverage table for one job. See [Generation](#generation).

### `POST /api/generations`

Retrieves evidence and generates the draft. The call is synchronous and can take a minute or more. Counts against `QUOTA_GENERATION`; makes two to four provider calls (query embedding, draft, at most one correction pass, optional verifier).

Header: `Idempotency-Key: <8 to 128 visible ASCII characters, no spaces>` (required). Use a new key for each user action and the same key when retrying that action.

Request: `{"job_id": "..."}`

| Situation | Response |
|---|---|
| First use of the key | `201` [Generation](#generation) with `status: "completed"` |
| Key already completed | `200` with the stored Generation; no quota charged, no provider call |
| Key still running | `409 generation_in_progress` with `details.generation_id`; poll `GET /api/generations/{id}` |
| Key failed earlier, or running for more than five minutes | runs again under the same `generation_id` |
| Key used before for a different job | `422 validation_error` (`job_id`) |

Errors: `400 idempotency_key_required`, `404 not_found` (job), `409 profile_not_confirmed`, `409 profile_not_indexed`, `429 quota_exceeded`, provider errors, `504 provider_timeout` when the whole generation exceeds 270 seconds. When a run fails, the generation is stored with `status: "failed"` and `error`, and the same key retries it.

### `GET /api/generations`

`200` `{"generations": [{"generation_id", "job_id", "job_title", "company", "status", "stale", "created_at"}]}`, newest first, at most 100.

### `GET /api/generations/{generation_id}`

`200` [Generation](#generation), or `404 not_found`. `stale` and `stale_reasons` are computed at read time. A running generation has `status: "running"` and `resume: null`; a failed one has `status: "failed"` and `error: {"code", "message"}`.

### `PATCH /api/generations/{generation_id}`

Saves manual edits and coverage corrections. Never regenerates text. No AI call.

```json
{
  "expected_revision": 1,
  "edits": [{"item_id": "...", "text": "..."}],
  "coverage_overrides": [{"requirement_id": "...", "status": "partial", "note": "Used it in coursework."}]
}
```

`edits[].text` is 1 to 1,200 characters (at most 200 edits). `coverage_overrides[].status` is `supported`, `partial`, `missing` or `uncertain`; `note` is up to 500 characters (at most 100 overrides). Both lists are optional.

`200` [Generation](#generation) with `revision` + 1.

- Each item whose text changed gets `validation_status: "user_edited"`, `user_edited: true` and no warnings; `validation.state` becomes `"needs_revalidation"`.
- Each overridden requirement gets the new status, `user_corrected: true` and the note; its evidence and rationale stay. `coverage_summary` is recomputed.

Errors: `404 not_found`, `409 version_conflict`, `409 generation_in_progress` (the draft is still being generated), `422 validation_error` (unknown `item_id` or `requirement_id`, with the field path; or the generation failed and has no documents).

### `POST /api/generations/{generation_id}/validate`

Checks every statement the user edited against the evidence it cites. Text is never changed. No body. Counts against `QUOTA_VALIDATION`; makes a provider call only when the semantic verifier is enabled.

`200` [Generation](#generation) with `revision` + 1, `validation.state: "validated"`, `validation.validated_at` set and `validation.user_edited_count: 0`. Each edited item ends as `supported`, `needs_review`, `unsupported` or (connective cover-letter text) `not_applicable`; its `user_edited` flag stays `true`.

The check uses the evidence of the profile version the draft was generated from, so a stale draft can still be validated.

Errors: `404 not_found`, `409 generation_in_progress`, `409 version_conflict` (the draft changed concurrently), `422 validation_error` (the generation has no documents), `429 quota_exceeded`.

### `POST /api/generations/{generation_id}/items/{item_id}/regenerate`

Rewrites one statement from the draft's stored evidence and validates the result. One AI call; counts against `QUOTA_REGENERATION`.

Header: `Idempotency-Key` (required, same format as above).

Request (optional body): `{"instruction": "Make it shorter."}`. `instruction` is at most 300 characters and is passed to the model as data; it cannot override the grounding rules.

`200` [Generation](#generation) with `revision` + 1. The item has new `text`, `evidence_ids`, `validation_status` and `warnings`, and `user_edited: false`. The result is stored with its real status: an `unsupported` result stays in the draft, flagged. A repeated key returns the stored draft without another provider call.

Only items in the summary, experience, projects and cover-letter sections can be regenerated. Education, certification and skill items come straight from the confirmed profile.

Errors: `400 idempotency_key_required`, `404 not_found` (draft or item), `409 generation_in_progress`, `409 version_conflict` (the draft changed concurrently), `422 validation_error` (item not regenerable, instruction too long, or the generation has no documents), `429 quota_exceeded`, provider errors (`502 provider_invalid_output` when the model returns an empty statement). After a failure the key is released and can be used again.

## Shared shapes

`T | null` means the field is always present and may be null.

### Profile

```text
Profile {
  profile_id: str
  version: int
  status: "draft" | "confirmed"
  index_state: "not_indexed" | "indexing" | "indexed" | "failed"
  indexed_version: int | null
  index_progress: { total: int, embedded: int }
  index_error: str | null
  contact: Contact
  records: [ProfileRecord]
  conflicts: [Conflict]
  sources: [{ source_id, label, source_type, char_count: int, revision: int }]
  review_summary: { needs_review_count: int, unresolved_conflict_count: int }
  created_at, updated_at, expires_at: iso
}

Contact { name, email, phone, location: str | null, links: [str] }

ProfileRecord {
  record_id: str
  category: "employment" | "education" | "project" | "publication" | "achievement" | "skill" | "certification"
  title: str
  organization, location, start_date, end_date, summary: str | null
  bullets: [{ bullet_id, text, source_ref: SourceRef | null, provenance, needs_review: bool, review_reasons: [str] }]
  skills: [str]
  source_ref: SourceRef | null
  provenance: "extracted" | "user_edited" | "user_added"
  needs_review: bool
  review_reasons: [str]
}

SourceRef { source_id, source_label, start: int, end: int, excerpt: str }

Conflict {
  conflict_id, field, description: str
  record_ids: [str]
  values: [{ value: str, source_ref: SourceRef | null }]
  resolution: "unresolved" | "resolved" | "dismissed"
  note: str | null
}
```

- A record with category `skill` is a skill group: `title` is the group label and `skills` holds the list.
- Dates are free text exactly as written in the source.
- `SourceRef.start` and `end` are offsets into the original pasted text, and `excerpt` is that slice.
- The profile is ready for generation when `status == "confirmed"`, `index_state == "indexed"` and `indexed_version == version`.
- `review_summary.needs_review_count` counts flagged records plus flagged bullets. Only unresolved conflicts block confirmation.

### Job

```text
Job {
  job_id: str
  version: int
  company, title: str | null
  description: str
  role_summary: str | null
  requirements: [Requirement]
  created_at, updated_at, expires_at: iso
}

Requirement {
  requirement_id: str
  text: str
  category: "skill" | "experience" | "education" | "certification" | "responsibility" | "other"
  importance: "required" | "preferred"
  inferred: bool
  keywords: [str]
  source_span: { start: int, end: int, excerpt: str } | null
  user_edited: bool
}
```

`source_span` offsets refer to `description`. `inferred` is true when the requirement is implied rather than stated, or when no passage of the posting could be located for it.

### Generation

```text
Generation {
  generation_id: str
  status: "running" | "completed" | "failed"
  error: { code, message } | null
  revision: int
  job_id: str, job_version: int, job_title: str | null, company: str | null
  profile_id: str, profile_version: int
  stale: bool
  stale_reasons: ["profile_changed" | "job_changed"]
  provider_mode: "openai" | "fake"
  model: str
  retrieved_evidence_ids: [str]
  resume: Resume | null
  cover_letter: { paragraphs: [Claim] } | null
  coverage: [CoverageItem]
  coverage_summary: { supported, partial, missing, uncertain, assessed: int, percent: number | null }
  validation: { state: "validated" | "needs_revalidation", needs_review_count, unsupported_count, user_edited_count: int, validated_at: iso | null }
  omitted_claims: [{ section, text, reason: str }]
  warnings: [str]
  usage: { input_tokens, output_tokens, embedding_tokens, provider_calls: int }
  created_at, updated_at, expires_at: iso
}

Resume {
  contact: Contact
  summary: [Claim]
  experience, projects, education, certifications: [ResumeEntry]
  skills: [Claim]
}

ResumeEntry {
  entry_id, record_id, category, heading: str
  subheading, location, date_range: str | null
  bullets: [Claim]
}

Claim {
  item_id: str
  text: str
  evidence_ids: [str]
  validation_status: "supported" | "needs_review" | "unsupported" | "user_edited" | "not_applicable"
  warnings: [str]
  user_edited: bool
}

CoverageItem {
  requirement_id, requirement_text, importance: str
  status: "supported" | "partial" | "missing" | "uncertain"
  evidence_ids: [str]
  rationale: str
  user_corrected: bool
  note: str | null
}
```

Reading a draft:

- **Headers are server-composed.** `heading`, `subheading`, `location` and `date_range` of every `ResumeEntry` are copied from the confirmed profile record (`heading` is the title, `subheading` the organisation). The model writes only bullets, summary lines, skills choices and cover-letter paragraphs.
- `experience` lists every confirmed employment record in profile order, even with no bullets. `projects` holds the project, publication and achievement records chosen for the job. `education` and `certifications` are confirmed text, unchanged.
- Every `evidence_id` can be opened with `GET /api/evidence/{id}`. IDs cited by skills, education and certifications may lie outside `retrieved_evidence_ids`.
- Greeting and closing paragraphs of the cover letter have `validation_status: "not_applicable"` and no citations.
- **Initial generation removes unsupported statements** and lists them in `omitted_claims`. An `unsupported` item can be present in a stored draft only after a validate or regenerate call.
- Before exporting, check `validation.state == "validated"`, `validation.unsupported_count == 0`, and have the user acknowledge any `needs_review_count > 0`.
- `coverage_summary.assessed = supported + partial + missing`; `percent = round(100 * (supported + 0.5 * partial) / assessed, 1)`, or `null` when nothing was assessed. It measures evidence coverage only: it is not a suitability, ATS or hiring score. A `missing` rationale always says that no evidence was found in the supplied profile.
- `warnings` are draft-level sentences, for example that citations were ignored or statements were left out.
- `model` is the generation model that produced the draft (`fake-llm-1` in demo mode). `usage` accumulates over the generation and later regenerations.
