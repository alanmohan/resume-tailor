/**
 * TypeScript mirror of the backend API contract (v1).
 *
 * Field names are snake_case exactly as they travel over the wire, and every
 * timestamp is an ISO-8601 UTC string ending in "Z". Keep this file in step
 * with the backend schemas; nothing here is invented by the frontend.
 */

export type IsoDateString = string

// ---------------------------------------------------------------- errors

export interface FieldError {
  field: string
  message: string
}

/** Body of every non-2xx response. */
export interface ErrorEnvelope {
  error: {
    code: string
    message: string
    request_id: string
    field_errors?: FieldError[]
    retryable?: boolean
    details?: Record<string, unknown>
  }
}

// ---------------------------------------------------------------- health

export type ProviderMode = 'openai' | 'fake'

export interface HealthStatus {
  status: 'ok'
}

export interface Limits {
  max_profile_chars: number
  max_job_chars: number
  max_sources: number
  max_requirements: number
  session_ttl_hours: number
}

/** Returned with 200 when ready and with 503 when not. */
export interface ReadyStatus {
  status: 'ready' | 'not_ready'
  checks: Record<string, string>
  provider_mode?: ProviderMode
  /** The server's configured limits, readable before a session exists. Absent on older servers. */
  limits?: Limits
}

// --------------------------------------------------------------- session

export interface SessionCreated {
  token: string
  expires_at: IsoDateString
  provider_mode: ProviderMode
  limits: Limits
}

export interface SessionInfo {
  expires_at: IsoDateString
  provider_mode: ProviderMode
  limits: Limits
  has_profile: boolean
}

export interface DeletedCounts {
  sources: number
  profiles: number
  evidence: number
  jobs: number
  generations: number
}

export interface DeleteSessionResult {
  deleted: true
  deleted_counts: DeletedCounts
}

// --------------------------------------------------------------- profile

export type SourceType = 'resume' | 'linkedin' | 'notes'

export interface SourceInput {
  label: string
  source_type: SourceType
  text: string
}

export interface IngestRequest {
  sources: SourceInput[]
}

export interface Contact {
  name: string | null
  email: string | null
  phone: string | null
  location: string | null
  links: string[]
}

/** start/end are offsets into the original stored source text. */
export interface SourceRef {
  source_id: string
  source_label: string
  start: number
  end: number
  excerpt: string
}

export type Provenance = 'extracted' | 'user_edited' | 'user_added'

export type RecordCategory =
  | 'employment'
  | 'education'
  | 'project'
  | 'publication'
  | 'achievement'
  | 'skill'
  | 'certification'

export interface ProfileBullet {
  bullet_id: string
  text: string
  source_ref: SourceRef | null
  provenance: Provenance
  needs_review: boolean
  review_reasons: string[]
}

export interface ProfileRecord {
  record_id: string
  category: RecordCategory
  title: string
  organization: string | null
  location: string | null
  /** Dates are kept exactly as written in the source, never normalised. */
  start_date: string | null
  end_date: string | null
  summary: string | null
  bullets: ProfileBullet[]
  skills: string[]
  source_ref: SourceRef | null
  provenance: Provenance
  needs_review: boolean
  review_reasons: string[]
}

export type ConflictResolution = 'unresolved' | 'resolved' | 'dismissed'

export interface ConflictValue {
  value: string
  source_ref: SourceRef | null
}

export interface Conflict {
  conflict_id: string
  field: string
  description: string
  record_ids: string[]
  values: ConflictValue[]
  resolution: ConflictResolution
  note: string | null
}

export interface ProfileSource {
  source_id: string
  label: string
  source_type: SourceType
  char_count: number
  revision: number
}

/** Something the extraction wants the user to know, e.g. source lines it could not capture. */
export interface ProfileNotice {
  code: string
  message: string
}

export type ProfileStatus = 'draft' | 'confirmed'
export type IndexState = 'not_indexed' | 'indexing' | 'indexed' | 'failed'

export interface Profile {
  profile_id: string
  version: number
  status: ProfileStatus
  index_state: IndexState
  indexed_version: number | null
  index_progress: { total: number; embedded: number }
  index_error: string | null
  contact: Contact
  records: ProfileRecord[]
  conflicts: Conflict[]
  sources: ProfileSource[]
  /** Absent when there are none; read it as `profile.notices ?? []`. */
  notices?: ProfileNotice[]
  review_summary: { needs_review_count: number; unresolved_conflict_count: number }
  created_at: IsoDateString
  updated_at: IsoDateString
  expires_at: IsoDateString
}

/** bullet_id is null for a bullet the user just added. */
export interface ProfileBulletInput {
  bullet_id: string | null
  text: string
}

/** record_id is null for a record the user just added. */
export interface ProfileRecordInput {
  record_id: string | null
  category: RecordCategory
  title: string
  organization: string | null
  location: string | null
  start_date: string | null
  end_date: string | null
  summary: string | null
  bullets: ProfileBulletInput[]
  skills: string[]
}

export interface ConflictResolutionInput {
  conflict_id: string
  resolution: 'resolved' | 'dismissed'
  note?: string | null
}

export interface ProfilePatchRequest {
  expected_version: number
  contact: Contact
  records: ProfileRecordInput[]
  conflict_resolutions?: ConflictResolutionInput[]
}

// ------------------------------------------------------------------ jobs

export type RequirementCategory =
  | 'skill'
  | 'experience'
  | 'education'
  | 'certification'
  | 'responsibility'
  | 'other'

export type RequirementImportance = 'required' | 'preferred'

export interface Requirement {
  requirement_id: string
  text: string
  category: RequirementCategory
  importance: RequirementImportance
  inferred: boolean
  keywords: string[]
  source_span: { start: number; end: number; excerpt: string } | null
  user_edited: boolean
}

export interface Job {
  job_id: string
  version: number
  company: string | null
  title: string | null
  description: string
  role_summary: string | null
  requirements: Requirement[]
  created_at: IsoDateString
  updated_at: IsoDateString
  expires_at: IsoDateString
}

export interface JobSummary {
  job_id: string
  title: string | null
  company: string | null
  version: number
  requirement_count: number
  created_at: IsoDateString
  updated_at: IsoDateString
}

/** GET /api/jobs. The app itself no longer lists jobs; the end-to-end tests read it. */
export interface JobListResponse {
  jobs: JobSummary[]
}

export interface JobCreateRequest {
  description: string
  company?: string | null
  title?: string | null
}

// ----------------------------------------------------------- generations

export type ValidationStatus =
  | 'supported'
  | 'needs_review'
  | 'unsupported'
  | 'user_edited'
  | 'not_applicable'

export type CoverageStatus = 'supported' | 'partial' | 'missing' | 'uncertain'

/** One generated sentence, bullet or paragraph with its provenance. */
export interface Claim {
  item_id: string
  text: string
  evidence_ids: string[]
  validation_status: ValidationStatus
  warnings: string[]
  user_edited: boolean
}

/** Heading fields are composed by the server from the confirmed profile. */
export interface ResumeEntry {
  entry_id: string
  record_id: string
  category: string
  heading: string
  subheading: string | null
  location: string | null
  date_range: string | null
  bullets: Claim[]
}

export interface Resume {
  contact: Contact
  summary: Claim[]
  experience: ResumeEntry[]
  projects: ResumeEntry[]
  education: ResumeEntry[]
  certifications: ResumeEntry[]
  skills: Claim[]
}

export interface CoverLetter {
  paragraphs: Claim[]
}

export interface CoverageItem {
  requirement_id: string
  requirement_text: string
  importance: string
  status: CoverageStatus
  evidence_ids: string[]
  rationale: string
  user_corrected: boolean
  note: string | null
}

/** percent is null when no requirement could be assessed. */
export interface CoverageSummary {
  supported: number
  partial: number
  missing: number
  uncertain: number
  assessed: number
  percent: number | null
}

export interface ValidationSummary {
  state: 'validated' | 'needs_revalidation'
  needs_review_count: number
  unsupported_count: number
  user_edited_count: number
  validated_at: IsoDateString | null
}

export interface OmittedClaim {
  section: string
  text: string
  reason: string
}

export interface Usage {
  input_tokens: number
  output_tokens: number
  embedding_tokens: number
  provider_calls: number
}

export type GenerationStatus = 'running' | 'completed' | 'failed'
export type StaleReason = 'profile_changed' | 'job_changed'

export interface Generation {
  generation_id: string
  status: GenerationStatus
  error: { code: string; message: string } | null
  revision: number
  job_id: string
  job_version: number
  job_title: string | null
  company: string | null
  profile_id: string
  profile_version: number
  stale: boolean
  stale_reasons: StaleReason[]
  provider_mode: ProviderMode
  model: string
  retrieved_evidence_ids: string[]
  resume: Resume | null
  cover_letter: CoverLetter | null
  coverage: CoverageItem[]
  coverage_summary: CoverageSummary
  validation: ValidationSummary
  omitted_claims: OmittedClaim[]
  warnings: string[]
  usage: Usage
  created_at: IsoDateString
  updated_at: IsoDateString
  expires_at: IsoDateString
}

export interface GenerationSummary {
  generation_id: string
  job_id: string
  job_title: string | null
  company: string | null
  status: GenerationStatus
  stale: boolean
  created_at: IsoDateString
}

export interface GenerationListResponse {
  generations: GenerationSummary[]
}

export interface GenerationCreateRequest {
  job_id: string
}

export interface GenerationPatchRequest {
  expected_revision: number
  edits?: { item_id: string; text: string }[]
  coverage_overrides?: {
    requirement_id: string
    status: CoverageStatus
    note?: string | null
  }[]
}

export interface RegenerateItemRequest {
  instruction?: string | null
}

// -------------------------------------------------------------- evidence

/** Supporting text for a citation. Embedding vectors are never returned. */
export interface Evidence {
  evidence_id: string
  excerpt: string
  text: string
  category: string
  source: {
    source_id: string | null
    label: string
    source_type: string
    start: number | null
    end: number | null
  }
  provenance: Provenance
  parent: {
    record_id: string
    category: string
    title: string
    organization: string | null
  } | null
  tags: string[]
  profile_version: number
}
