# Backend test matrix

Maps every item of the "Required meaningful tests" list in
`IMPLEMENTATION_SPEC.md` (section 9) to the backend tests that cover it.
References are `file::test name`, relative to `backend/tests/`.
`unit/test_test_matrix.py` fails if a reference in this file does not exist,
so the table cannot drift away from the code.

How the tests run:

```bash
cd backend
python -m pytest tests/unit -q          # pure functions, no database
python -m pytest tests/integration -q   # real routes + local MongoDB, fake AI provider
```

- Integration tests call the application in-process through its real routes
  and middleware, against the docker `mongo` service, in a database named
  `resume_tailor_test_<random>` that is dropped afterwards.
- The AI provider is always the deterministic fake, so the suites cost
  nothing. Tests named "... whatever the model writes" replace only document
  generation with a scripted, deliberately dishonest model output; that is how
  the server-side checks are shown to hold independently of the model.
- Nothing here calls OpenAI. Real-provider checks are separate opt-in smoke
  tests (`-m smoke`) and are not part of this matrix.
- Items about the browser (print layout, 375px, keyboard, deep links, escaping
  in the DOM) are tested in `frontend/`; the backend half is listed where
  there is one.

## Spec section 9: required meaningful tests

### 1. Ingest sources, edit/confirm profile, index, analyze job, generate, inspect evidence, edit/revalidate, print, clear data

| What is checked | Test |
|---|---|
| The whole workflow over HTTP with the fictional fixtures: session, ingest, get, edit, resolve conflict, confirm, job, requirement review, generate, replay, every citation opened, manual edit, validate, regenerate, stale after profile edit, clear data, every later call 401 (except a repeated clear, which answers 200 with zero counts), collections empty | `integration/test_full_flow.py::test_visitor_goes_from_sources_to_a_revalidated_draft_and_clears_their_data` |
| Profile half in detail | `integration/test_profile_flow.py::test_ingest_review_confirm_and_open_a_citation` |
| Draft content: confirmed headers, citations, coverage, usage | `integration/test_generation_create.py::test_generation_returns_a_grounded_draft` |
| Print | frontend only (the API returns structured text; there is no server-side rendering) |

### 2. A job asks for Kubernetes; profile mentions Docker only: no invented Kubernetes experience

| What is checked | Test |
|---|---|
| Fixture profile + `stretch_kubernetes` job, honest run: no forbidden term in the documents, Docker requirement supported by Docker evidence, Kubernetes requirements reported as missing with the "not found in the supplied profile" wording | `integration/test_fixture_grounding.py::test_kubernetes_job_gets_docker_evidence_and_an_honest_gap` |
| Same pipeline, model output that stretches Docker evidence into Kubernetes claims: removed into `omitted_claims`, one bounded correction pass, coverage downgraded, uncited "connective" mention flagged | `integration/test_fixture_grounding.py::test_invented_kubernetes_experience_is_removed_whatever_the_model_writes` |
| Seeded profile, same property | `integration/test_generation_grounding.py::test_invented_kubernetes_experience_never_reaches_the_documents` |
| A manual edit that adds Kubernetes becomes unsupported on validation | `integration/test_generation_editing.py::test_validate_changes_statuses_but_never_text` |
| The rule itself | `unit/test_validation_claims.py::test_kubernetes_claim_is_unsupported_when_the_profile_only_mentions_docker`, `unit/test_coverage_rules.py::test_kubernetes_is_not_supported_by_evidence_that_only_mentions_docker` |

### 3. A source contains a 20% metric unrelated to the generated claim: reject the misleading numerical reuse

| What is checked | Test |
|---|---|
| Fixture profile + `metric_trap` job, honest run: the figure only ever appears with "unit test coverage"; cost requirements are not rated as covered | `integration/test_fixture_grounding.py::test_cost_reduction_job_does_not_get_the_test_coverage_percentage` |
| Same pipeline, model output that moves the 20% onto cloud costs, the AWS bill and infrastructure spend: every such statement removed with the reason, the true statement kept, coverage downgraded | `integration/test_fixture_grounding.py::test_reused_percentage_is_rejected_whatever_the_model_writes` |
| Seeded profile, same property | `integration/test_generation_grounding.py::test_unrelated_twenty_percent_metric_is_rejected` |
| The rule: value, unit and the words next to the figure | `unit/test_validation_claims.py::test_twenty_percent_from_an_unrelated_statement_is_rejected`, `unit/test_validation_claims.py::test_only_the_words_next_to_the_evidence_figure_say_what_it_measures`, `unit/test_validation_claims.py::test_same_value_with_another_unit_is_rejected` |
| A role title stored with the evidence does not relate a claim to a figure | `unit/test_generation_compose.py::test_role_title_in_the_indexed_text_does_not_relate_a_claim_to_a_figure` |
| A requirement that names a metric | `unit/test_coverage_rules.py::test_metric_named_by_a_requirement_must_be_shown_for_the_same_thing` |

### 4. Instructions hidden in resume/job text cannot override grounding rules or access other profiles

| What is checked | Test |
|---|---|
| Fixture resume and posting with planted instructions, honest run: the planted text exists only in the stored source and job description; no extracted fact, evidence record, requirement or draft field contains it; real gaps stay gaps | `integration/test_fixture_grounding.py::test_planted_instructions_change_nothing_in_an_honest_run` |
| A model that obeys the planted instructions (invented years, PhD, canaries, "everything supported", another visitor's evidence ID and facts): overruled; the other visitor's data untouched | `integration/test_fixture_grounding.py::test_model_that_obeys_the_planted_instructions_is_overruled` |
| Resume: stored as text, never extracted | `integration/test_ingest_api.py::test_instruction_hidden_in_a_resume_is_stored_as_text_and_never_extracted` |
| Posting: nothing but the stored text changes | `integration/test_job_api.py::test_instructions_hidden_in_a_posting_change_nothing_but_the_stored_text` |
| Seeded profile and job | `integration/test_generation_grounding.py::test_instructions_hidden_in_profile_and_job_text_do_not_override_grounding` |
| Untrusted text travels only as JSON data, never inside the instructions | `unit/test_ingest_openai_ops.py::test_resume_text_reaches_the_model_only_as_json_data`, `unit/test_ingest_openai_ops.py::test_source_text_cannot_break_out_of_its_json_string`, `unit/test_generation_openai_ops.py::test_generation_request_keeps_untrusted_text_inside_the_json_input`, `unit/test_generation_openai_ops.py::test_regeneration_request_treats_the_style_instruction_as_data` |
| Deterministic guard for instruction-like lines | `unit/test_ingest_instruction_guard.py::test_statement_quoted_from_a_planted_line_does_not_become_a_fact`, `unit/test_ingest_instruction_guard.py::test_requirement_quoted_from_a_planted_paragraph_is_not_a_requirement` |
| Text written by the model for the coverage panel is checked too | `unit/test_coverage_rules.py::test_rationale_naming_what_is_in_neither_requirement_nor_evidence_is_replaced` |
| A regeneration that adds an invented skill is stored as unsupported with the reason, never as supported | `integration/test_generation_editing.py::test_regeneration_cannot_be_talked_into_unsupported_content` |

### 5. Unknown evidence ID, cross-user ID, expired/revoked token, mixed profile versions, and unindexed edits are rejected

| What is checked | Test |
|---|---|
| Unknown evidence ID and another session's evidence ID: the same 404 | `integration/test_evidence_api.py::test_evidence_of_another_session_looks_exactly_like_a_missing_one` |
| Evidence references invented by the model, or copied from another session | `integration/test_generation_grounding.py::test_unknown_aliases_and_another_sessions_evidence_ids_are_dropped`, `unit/test_generation_compose.py::test_unknown_aliases_and_foreign_ids_are_dropped_and_counted` |
| Cross-user job, generation and item IDs | `integration/test_full_flow.py::test_two_sessions_stay_isolated_from_sources_to_deletion`, `integration/test_generation_editing.py::test_another_session_cannot_reach_a_generation_in_any_way`, `integration/test_generation_create.py::test_unknown_and_foreign_jobs_are_not_found` |
| Missing, malformed and unknown tokens | `integration/test_sessions.py::test_missing_token_is_unauthorized`, `integration/test_sessions.py::test_malformed_authorization_header_is_unauthorized`, `integration/test_sessions.py::test_unknown_token_is_unauthorized`, `integration/test_sessions.py::test_token_is_never_accepted_from_the_query_string` |
| Expired token, before TTL cleanup, on every route group | `integration/test_sessions.py::test_expired_session_is_rejected_even_before_ttl_cleanup`, `integration/test_profile_job_auth.py::test_expired_token_is_rejected_before_ttl_cleanup`, `integration/test_generation_failures.py::test_expired_token_is_rejected_on_every_generation_route` |
| Revoked token, on every route except a repeated `DELETE /api/session` | `integration/test_sessions.py::test_delete_session_revokes_the_token`, `integration/test_profile_job_auth.py::test_revoked_token_is_rejected`, `integration/test_generation_failures.py::test_revoked_or_missing_token_is_rejected_on_every_generation_route`, `integration/test_full_flow.py::test_visitor_goes_from_sources_to_a_revalidated_draft_and_clears_their_data` |
| Mixed profile versions and mixed embedding models | `integration/test_generation_create.py::test_only_the_current_profile_versions_evidence_is_used`, `integration/test_generation_create.py::test_partly_indexed_or_mixed_model_evidence_blocks_generation`, `integration/test_retrieval_scoping.py::test_another_profile_versions_evidence_is_never_returned`, `integration/test_retrieval_scoping.py::test_evidence_embedded_with_another_model_is_refused`, `integration/test_index_confirm_api.py::test_vectors_of_another_embedding_model_are_never_reused` |
| Unindexed edits | `integration/test_generation_create.py::test_profile_edited_after_confirmation_blocks_generation`, `integration/test_generation_create.py::test_generation_requires_a_confirmed_and_indexed_profile`, `integration/test_index_confirm_api.py::test_edit_during_indexing_prevents_the_old_version_from_being_marked_indexed` |

### 6. Two sessions remain isolated for sources, retrieval, jobs, drafts, and deletion

| What is checked | Test |
|---|---|
| Two visitors run the whole pipeline side by side with different fixtures and the same Idempotency-Key: sources, profile, retrieval, evidence, jobs, drafts, lists and deletion | `integration/test_full_flow.py::test_two_sessions_stay_isolated_from_sources_to_deletion` |
| Sources and profile | `integration/test_profile_api.py::test_two_sessions_cannot_see_or_change_each_others_profile`, `integration/test_sessions.py::test_has_profile_is_scoped_to_the_caller` |
| Retrieval | `integration/test_retrieval_scoping.py::test_another_owners_evidence_is_never_returned` |
| Jobs | `integration/test_job_api.py::test_two_sessions_cannot_see_or_change_each_others_jobs` |
| Drafts | `integration/test_generation_editing.py::test_another_session_cannot_reach_a_generation_in_any_way`, `integration/test_generation_editing.py::test_same_idempotency_key_in_two_sessions_gives_two_separate_drafts` |
| Deletion | `integration/test_clear_data.py::test_delete_leaves_other_sessions_untouched`, `integration/test_generation_editing.py::test_deleting_one_session_leaves_the_other_sessions_drafts` |
| Every repository query filters on the owner | `integration/test_repositories.py::test_documents_are_invisible_to_other_owners`, `integration/test_repositories.py::test_another_owner_cannot_replace_or_modify_a_document`, `integration/test_repositories.py::test_a_document_cannot_be_stored_under_another_owner` |

### 7. Profile edits mark old drafts stale; validation status changes after document edits

| What is checked | Test |
|---|---|
| Stale after a profile edit and after a job edit, with the reason | `integration/test_generation_create.py::test_profile_and_job_edits_mark_existing_drafts_stale`, `integration/test_generation_create.py::test_draft_is_stale_when_the_profile_was_replaced`, `unit/test_generation_compose.py::test_draft_is_stale_when_profile_or_job_version_changed` |
| In the whole workflow | `integration/test_full_flow.py::test_visitor_goes_from_sources_to_a_revalidated_draft_and_clears_their_data` |
| A manual edit loses its verdict until revalidated | `integration/test_generation_editing.py::test_manual_edit_marks_the_item_user_edited_and_needs_revalidation` |
| Revalidation changes statuses, never text | `integration/test_generation_editing.py::test_validate_changes_statuses_but_never_text`, `integration/test_generation_editing.py::test_fixing_an_edit_and_validating_again_restores_support`, `integration/test_generation_editing.py::test_edit_that_borrows_from_elsewhere_in_the_profile_needs_review` |
| A stale draft is validated against the evidence it was built from | `integration/test_generation_editing.py::test_stale_draft_is_validated_against_the_evidence_it_was_built_from` |

### 8. Empty/long input, invalid schema, provider timeout/429, database failure, duplicate generation, zero coverage denominator, and no evidence results have useful behavior

| What is checked | Test |
|---|---|
| Empty input | `integration/test_ingest_api.py::test_blank_source_message_says_what_is_wrong`, `integration/test_job_api.py::test_empty_description_message_says_what_is_wrong`, `integration/test_index_confirm_api.py::test_profile_without_content_cannot_be_confirmed` |
| Long input, with both sizes in the message | `integration/test_ingest_api.py::test_profile_text_over_the_limit_is_rejected_with_both_sizes`, `integration/test_job_api.py::test_description_over_the_limit_is_rejected_with_both_sizes`, `integration/test_ingest_api.py::test_too_many_sources_are_rejected`, `integration/test_index_confirm_api.py::test_profile_with_too_many_chunks_is_rejected_with_the_limit`, `integration/test_cors_and_body_limit.py::test_declared_oversized_body_is_rejected_before_it_is_read`, `integration/test_cors_and_body_limit.py::test_streamed_oversized_body_without_content_length_is_rejected` |
| Invalid schema, with the field at fault | `integration/test_errors.py::test_schema_violations_return_field_errors`, `integration/test_errors.py::test_invalid_json_is_a_validation_error`, `integration/test_ingest_api.py::test_invalid_sources_are_rejected_with_the_field_at_fault`, `integration/test_profile_api.py::test_invalid_edits_are_rejected_with_the_field_at_fault`, `integration/test_job_api.py::test_invalid_reviews_are_rejected`, `integration/test_generation_create.py::test_invalid_body_is_a_validation_error` |
| Provider timeout and rate limit on ingest, confirm, job analysis, generation and regeneration: retryable error, finished work kept, the same request succeeds on retry | `integration/test_failure_recovery.py::test_provider_failure_keeps_finished_work_and_the_same_request_succeeds_on_retry` |
| Provider error codes and statuses | `integration/test_errors.py::test_provider_errors_map_to_their_status_and_code`, `integration/test_generation_failures.py::test_provider_failure_marks_the_generation_failed_and_a_retry_succeeds`, `integration/test_index_confirm_api.py::test_embedding_failure_is_recorded_and_a_retry_resumes`, `integration/test_index_confirm_api.py::test_rate_limited_embedding_maps_to_503_and_keeps_the_profile` |
| A real failure is never replaced by fake output | `unit/test_ingest_openai_ops.py::test_provider_failure_is_reported_not_replaced`, `unit/test_openai_client.py::test_sdk_failures_are_translated`, `unit/test_openai_client.py::test_a_call_that_exceeds_the_deadline_times_out` |
| Database failure: liveness stays up, readiness and the API answer 503 retryable | `integration/test_failure_recovery.py::test_routes_report_an_unreachable_database_as_retryable`, `integration/test_health.py::test_unreachable_database_keeps_liveness_but_fails_readiness`, `integration/test_errors.py::test_database_connection_failure_returns_database_unavailable`, `integration/test_generation_failures.py::test_database_failure_while_saving_is_reported_and_retryable` |
| Duplicate generation: one paid run per Idempotency-Key | `integration/test_generation_create.py::test_repeating_a_key_returns_the_stored_draft_without_a_second_generation`, `integration/test_generation_create.py::test_concurrent_duplicates_run_one_generation`, `integration/test_generation_create.py::test_duplicate_while_running_gets_409_and_the_draft_once_finished`, `integration/test_generation_create.py::test_missing_or_malformed_idempotency_key_is_rejected`, `integration/test_repositories.py::test_simultaneous_requests_with_one_key_yield_a_single_claim` |
| A generation cannot outlive the window in which its key is reserved | `integration/test_failure_recovery.py::test_generation_that_runs_too_long_fails_as_a_timeout_and_can_be_retried`, `integration/test_generation_failures.py::test_running_record_older_than_five_minutes_is_taken_over` |
| Zero coverage denominator: percentage unavailable, not 0 | `unit/test_coverage_rules.py::test_zero_denominator_gives_no_percentage`, `integration/test_generation_create.py::test_job_without_requirements_has_no_coverage_percentage` |
| No evidence results | `integration/test_generation_create.py::test_profile_without_evidence_still_yields_a_valid_draft`, `integration/test_generation_create.py::test_unrelated_profile_gets_missing_coverage_not_invented_content`, `integration/test_retrieval_scoping.py::test_profile_without_evidence_gives_an_empty_context`, `unit/test_retrieval_ranking.py::test_cosine_of_a_zero_row_is_zero_not_nan` |
| Quotas and the global AI-call cap | `integration/test_ingest_api.py::test_ingest_quota_is_enforced_per_session`, `integration/test_index_confirm_api.py::test_confirm_quota_is_enforced_per_session`, `integration/test_job_api.py::test_job_analysis_quota_is_enforced_per_session`, `integration/test_generation_create.py::test_generation_quota_is_enforced_per_session`, `integration/test_generation_editing.py::test_regeneration_quota_is_enforced`, `integration/test_generation_create.py::test_global_daily_ai_limit_stops_generation_before_the_provider`, `integration/test_rate_limits.py::test_session_creation_is_rate_limited_per_client` |

### 9. Restart backend and confirm non-expired MongoDB records survive; clearing data prevents concurrent generation from writing them back

| What is checked | Test |
|---|---|
| A second application instance on the same database serves the profile, job, draft and evidence; a repeated Idempotency-Key is answered from storage with no provider call; an expired session is refused | `integration/test_full_flow.py::test_restarted_server_serves_stored_work_and_replays_without_a_provider_call` |
| Profile only | `integration/test_profile_flow.py::test_stored_records_survive_a_server_restart` |
| Clearing data while a generation is in flight | `integration/test_generation_failures.py::test_clearing_data_during_a_slow_generation_leaves_nothing_behind`, `integration/test_generation_failures.py::test_clearing_data_during_a_failing_generation_leaves_nothing_behind` |
| The same for ingestion, indexing, job analysis, regeneration and validation | `integration/test_ingest_api.py::test_clearing_data_while_ingesting_leaves_nothing_behind`, `integration/test_index_confirm_api.py::test_clearing_data_while_indexing_leaves_no_evidence_behind`, `integration/test_job_api.py::test_clearing_data_while_a_job_is_analysed_leaves_nothing_behind`, `integration/test_failure_recovery.py::test_clearing_data_during_a_slow_regeneration_leaves_nothing_behind`, `integration/test_failure_recovery.py::test_clearing_data_during_a_slow_validation_leaves_nothing_behind` |
| The post-write guard itself | `integration/test_clear_data.py::test_write_after_deletion_is_cleaned_up_by_the_guard`, `integration/test_clear_data.py::test_clearing_data_during_a_slow_operation_leaves_nothing_behind` |
| A client disconnect does not abort work half way | `integration/test_errors.py::test_in_flight_work_finishes_when_the_client_disconnects` |

### 10. Mobile 375px, keyboard-only interaction, deep-link refresh, source-text escaping, and multi-page print layout work

| What is checked | Test |
|---|---|
| Layout, keyboard, deep links, print | frontend only |
| Source-text escaping, server half: markup in pasted text is stored and returned unchanged as JSON strings, and no API response may be sniffed as HTML or cached | `integration/test_plain_text_and_headers.py::test_markup_in_pasted_text_stays_plain_text_from_ingestion_to_the_draft`, `integration/test_plain_text_and_headers.py::test_api_responses_are_json_that_must_not_be_sniffed_or_cached` |

### 11. Optional features receive relevant parser, ownership, persistence, and failure tests when built

No optional feature of spec section 7 is built in the backend, so there is nothing to test here.

## Spec section 2E: privacy, isolation and graceful failure

| Property | Test |
|---|---|
| Random token, only its hash stored | `integration/test_sessions.py::test_only_the_token_hash_is_stored`, `integration/test_sessions.py::test_the_token_hash_itself_is_not_accepted_as_a_token`, `unit/test_security.py::test_tokens_are_long_url_safe_and_unique` |
| `expires_at` and a TTL index on every owned collection | `integration/test_indexes.py::test_every_expiring_collection_has_a_ttl_index_on_expires_at`, `integration/test_generation_create.py::test_generation_is_stored_with_owner_expiry_and_audit_fields` |
| Clear my data: revoke first, delete everything | `integration/test_clear_data.py::test_delete_removes_documents_from_every_owned_collection`, `integration/test_clear_data.py::test_deleted_data_cannot_be_read_back_with_the_old_token` |
| Exact CORS origins, including the Idempotency-Key preflight | `integration/test_cors_and_body_limit.py::test_preflight_from_allowed_origin_permits_the_api_headers`, `integration/test_cors_and_body_limit.py::test_preflight_from_disallowed_origin_is_refused`, `integration/test_cors_and_body_limit.py::test_similar_looking_origins_are_not_allowed` |
| Persistent counters for public AI endpoints | `integration/test_rate_limits.py::test_operation_quota_is_persisted_on_the_session`, `integration/test_rate_limits.py::test_concurrent_requests_cannot_exceed_the_quota`, `integration/test_rate_limits.py::test_global_ai_call_cap_is_persisted_and_enforced` |
| Embedding vectors never leave the server | `integration/test_evidence_api.py::test_embedding_vectors_are_never_returned` |
| Contact details stripped from embedding inputs | `unit/test_index_evidence_builder.py::test_contact_details_are_removed_from_the_embedded_text_only`, `integration/test_retrieval_scoping.py::test_contact_details_in_a_posting_are_not_sent_for_embedding` |
| Logs: request ID, route template, status, duration, usage; no tokens, bodies or exception messages | `integration/test_errors.py::test_access_log_records_the_route_template_not_the_url`, `integration/test_errors.py::test_production_logs_omit_exception_messages`, `unit/test_logging.py::test_redact_masks_bearer_tokens_api_keys_and_mongodb_uris`, `unit/test_openai_client.py::test_usage_is_logged_without_prompt_or_response_content` |
| Secrets stay out of error messages and settings output | `unit/test_config.py::test_load_settings_reports_the_setting_name_but_never_values`, `unit/test_config.py::test_secrets_are_not_shown_when_settings_are_printed`, `integration/test_health.py::test_health_endpoints_do_not_expose_configuration` |

## Review findings fixed on 2026-10-07

Tests added or changed for the findings of the review and of the first real
evaluation run. IDs are those of the review reports (`docs/review_*.json`,
`docs/evaluate_experience-master.json`). New files are named `test_fix_<area>_*.py`.

### Coverage panel

| Finding | What is checked | Test |
|---|---|---|
| G-01 | Whole phrases are compared, not word fragments; descriptor words are not keywords | `unit/test_fix_coverage_phrases.py::test_compound_descriptors_are_not_keywords_to_find`, `unit/test_fix_coverage_phrases.py::test_honest_rating_and_its_sentence_are_kept`, `unit/test_fix_coverage_phrases.py::test_no_rationale_shows_word_fragments` |
| EM-02 | Alternatives ("or", "such as", brackets, "at least one") need one mention; open-ended lists demand nothing; the hiring company's name and non-keyword names in a duty are not demanded | `unit/test_fix_coverage_phrases.py::test_such_as_makes_the_category_and_its_example_alternatives`, `unit/test_fix_coverage_phrases.py::test_bracketed_examples_and_quantifiers_are_any_of`, `unit/test_fix_coverage_phrases.py::test_or_joins_alternatives_and_and_separates_them`, `unit/test_fix_coverage_phrases.py::test_open_ended_lists_demand_nothing`, `unit/test_fix_coverage_phrases.py::test_the_hiring_company_is_not_expected_in_the_profile`, `unit/test_fix_coverage_phrases.py::test_only_keywords_are_checked_in_a_duty` |
| EM-04 | A lone keyword absent from the whole profile is "missing"; present elsewhere it is "uncertain"; a duty is never turned into "missing"; "partial" for a duty with nothing checkable is "uncertain"; the server never raises a rating | `unit/test_fix_coverage_phrases.py::test_lone_technology_absent_from_the_whole_profile_is_missing`, `unit/test_fix_coverage_phrases.py::test_technology_elsewhere_in_the_profile_is_uncertain_not_missing`, `unit/test_fix_coverage_phrases.py::test_a_duty_is_never_turned_into_missing_by_the_server`, `unit/test_fix_coverage_phrases.py::test_partial_for_a_duty_with_nothing_to_check_is_uncertain`, `unit/test_fix_coverage_phrases.py::test_the_server_never_raises_a_rating` |
| EM-09 | The model's sentence is kept and the server's reason appended; it is replaced only for names, keywords and figures from nowhere | `unit/test_fix_coverage_phrases.py::test_supported_becomes_partial_and_keeps_the_models_sentence`, `unit/test_fix_coverage_phrases.py::test_sentence_using_a_word_of_another_requirements_keyword_is_kept`, `unit/test_fix_coverage_phrases.py::test_sentence_may_name_the_job_but_not_another_requirements_technology`, `unit/test_fix_coverage_phrases.py::test_models_sentence_is_dropped_when_it_names_what_the_evidence_lacks`, `unit/test_fix_coverage_phrases.py::test_planted_text_and_invented_figures_are_still_replaced` |
| G-12 | Demo mode names whole keywords in its rationales | `unit/test_fix_coverage_fake_rationale.py::test_rationale_names_whole_keywords`, `unit/test_fix_coverage_fake_rationale.py::test_keyword_needs_all_its_words_in_one_record` |

### Statement validation

| Finding | What is checked | Test |
|---|---|---|
| G-06 | A figure typed with its unit ("420ms") matches the spaced evidence and the reverse; an invented one is rejected as a figure | `unit/test_fix_validation_claims.py::test_figure_with_attached_unit_matches_the_spaced_evidence`, `unit/test_fix_validation_claims.py::test_spaced_figure_matches_evidence_written_with_an_attached_unit`, `unit/test_fix_validation_claims.py::test_invented_figure_with_attached_unit_is_rejected_as_a_figure`, `unit/test_fix_validation_claims.py::test_same_value_in_another_notation_is_not_rejected` |
| G-07 | Halves of compound descriptors are not required terms; distinctive compounds still are | `unit/test_fix_validation_claims.py::test_descriptor_compounds_leave_no_requirement_terms`, `unit/test_fix_validation_claims.py::test_distinctive_compound_keyword_is_still_required` |
| G-03 | A skill named in the job title is not unlocked in the cover letter; the literal title may still be written | `unit/test_fix_validation_claims.py::test_skill_named_in_the_job_title_is_not_unlocked_in_the_cover_letter`, `unit/test_fix_validation_claims.py::test_literal_job_title_and_company_may_be_named_in_the_cover_letter` |
| G-08 | A figure moved to another result is rejected even when the method clause is shared | `unit/test_fix_validation_claims.py::test_figure_moved_to_another_result_is_rejected_even_with_the_same_method`, `unit/test_fix_validation_claims.py::test_honest_rewordings_of_the_measured_result_are_accepted` |
| G-10 | Seniority wording needs the evidence's own words | `unit/test_fix_validation_claims.py::test_seniority_not_in_the_evidence_needs_review`, `unit/test_fix_validation_claims.py::test_seniority_stated_by_the_evidence_or_its_record_title_is_supported` |

### Drafting and retrieval

| Finding | What is checked | Test |
|---|---|---|
| G-02 | No bare role heading: a role without bullets gets its own confirmed bullets; first-person remarks are skipped; an empty project is dropped; each role's best statement enters the context | `unit/test_fix_drafting_rules.py::test_role_without_bullets_gets_its_own_confirmed_bullets_word_for_word`, `unit/test_fix_drafting_rules.py::test_first_person_remarks_are_not_printed_as_bullets`, `unit/test_fix_drafting_rules.py::test_project_without_bullets_is_dropped_unless_its_title_is_all_there_is`, `unit/test_fix_drafting_rules.py::test_every_role_has_a_statement_in_the_context_when_there_is_room`, `integration/test_fix_drafting_generation.py::test_role_the_model_skipped_shows_its_confirmed_bullets_and_can_be_regenerated`, `unit/test_generation_fake_ops.py::test_first_person_remark_is_never_printed_as_a_bullet` |
| EM-01 | Skills listed under a role are evidence, can be cited, and are accepted without a correction pass | `unit/test_fix_platform_skill_evidence.py::test_skills_under_a_role_become_one_evidence_record_of_that_role`, `unit/test_fix_platform_skill_evidence.py::test_a_skill_listed_only_under_a_role_can_be_cited`, `integration/test_fix_platform_skill_evidence_api.py::test_role_skills_are_stored_embedded_and_can_be_opened`, `unit/test_fix_drafting_rules.py::test_skill_listed_under_a_role_is_supported_and_cites_that_role`, `integration/test_fix_drafting_generation.py::test_skill_listed_under_a_role_is_accepted_without_a_correction_pass` |
| G-05 | A coursework-only skill is not offered to the model and needs review without its qualifier | `unit/test_fix_drafting_rules.py::test_coursework_only_skills_are_not_offered_to_the_model`, `unit/test_fix_drafting_rules.py::test_coursework_only_skill_needs_review_unless_it_keeps_its_qualifier`, `integration/test_fix_drafting_generation.py::test_coursework_only_skill_is_not_offered_and_is_flagged_if_the_model_uses_it` |
| G-04 | A sentence that presents a project as work at an employer needs review | `unit/test_fix_drafting_rules.py::test_sentence_that_moves_a_project_to_an_employer_needs_review`, `integration/test_fix_drafting_generation.py::test_project_presented_as_work_at_an_employer_is_flagged_in_summary_and_letter` |
| SPEC-01 | An edited, uncited paragraph that states a qualification needs review; a plain greeting stays not applicable | `unit/test_fix_drafting_rules.py::test_statements_about_the_applicant_are_not_courtesy_text`, `integration/test_fix_drafting_generation.py::test_edited_uncited_paragraph_that_states_a_qualification_needs_review` |
| EM-07 | A bullet that repeats an earlier bullet of its entry is flagged | `unit/test_fix_drafting_rules.py::test_bullet_that_repeats_an_earlier_bullet_is_flagged_and_the_first_is_kept` |
| EM-06 | A long summary becomes several evidence records with exact source spans | `unit/test_fix_platform_summary_chunks.py::test_each_paragraph_of_a_long_summary_is_its_own_evidence_record`, `unit/test_fix_platform_summary_chunks.py::test_a_bullet_of_the_same_length_is_not_split` |

### Ingestion and job analysis

| Finding | What is checked | Test |
|---|---|---|
| EM-03 | Source lines the extraction skipped are reported in a notice; the prompt treats list items under any record as bullets | `unit/test_fix_ingest_draft.py::test_result_lines_the_model_skipped_under_a_publication_are_reported`, `unit/test_fix_ingest_draft.py::test_completely_extracted_source_gives_no_notice`, `integration/test_fix_ingest_notices.py::test_text_the_extraction_skipped_is_reported_without_blocking_confirmation`, `unit/test_fix_ingest_prompts_and_fake.py::test_extraction_prompt_treats_list_items_under_any_record_as_bullets` |
| G-09 | A date conflict the model also reported is listed once | `unit/test_fix_ingest_draft.py::test_date_conflict_reported_by_the_model_is_listed_once`, `unit/test_fix_ingest_draft.py::test_a_different_disagreement_about_the_same_role_stays_a_second_entry` |
| SPEC-03 | Contact details that differ between sources are a conflict that blocks confirmation | `unit/test_fix_ingest_draft.py::test_different_email_addresses_across_sources_are_a_conflict`, `unit/test_fix_ingest_draft.py::test_contact_values_are_compared_by_meaning`, `integration/test_fix_ingest_notices.py::test_conflicting_contact_details_must_be_resolved_before_confirming` |
| EM-08 | A profile without name, contact details or education gets notices that disappear when the detail is added | `unit/test_fix_ingest_draft.py::test_profile_without_name_contact_or_education_gets_three_notices`, `integration/test_fix_ingest_notices.py::test_adding_the_missing_details_removes_their_notices`, `integration/test_profile_flow.py::test_profile_response_has_the_contract_shape` |
| EM-11, EM-13 | Publication status and role lines are kept; the prose fixture is fully captured | `unit/test_fix_ingest_draft.py::test_publication_status_and_role_line_are_kept_as_written`, `unit/test_fix_ingest_prompts_and_fake.py::test_prose_fixture_is_fully_captured_and_only_lacks_contact_and_education`, `integration/test_fix_ingest_notices.py::test_prose_fixture_keeps_publication_results_and_record_skills` |
| EM-05 | Duties are stored as inferred and cut first; the prompt excludes culture statements | `unit/test_fix_ingest_prompts_and_fake.py::test_duty_is_stored_as_inferred_even_when_the_model_says_it_is_stated`, `unit/test_fix_ingest_prompts_and_fake.py::test_duties_are_cut_before_stated_qualifications_when_the_list_is_capped`, `unit/test_fix_ingest_prompts_and_fake.py::test_job_prompt_excludes_culture_and_benefit_statements` |

### Platform, sessions and configuration

| Finding | What is checked | Test |
|---|---|---|
| SEC-3 | A forged first `X-Forwarded-For` hop does not change the client address or evade the session limit | `unit/test_fix_platform_client_ip.py::test_a_forged_leftmost_hop_does_not_change_the_address`, `unit/test_fix_platform_client_ip.py::test_every_header_is_ignored_unless_proxies_are_trusted`, `integration/test_fix_platform_client_ip.py::test_rotating_a_forged_first_hop_still_reaches_the_limit` |
| SEC-4 | A broken character in source or job text is a field error before any charge | `integration/test_fix_platform_broken_characters.py::test_broken_character_in_resume_text_is_a_field_error_before_any_charge`, `integration/test_fix_platform_broken_characters.py::test_broken_character_in_a_job_description_is_a_field_error_before_any_charge` |
| SEC-2 | "Clear my data" can be retried after a database error; a revoked token can do nothing else | `integration/test_fix_platform_clear_retry.py::test_clear_can_be_retried_after_a_database_error`, `integration/test_fix_platform_clear_retry.py::test_a_revoked_token_can_only_clear_its_own_data`, `integration/test_fix_platform_clear_retry.py::test_clear_is_refused_for_expired_and_unknown_tokens` |
| SEC-1 | Old evidence versions are pruned at confirm; a version a stored draft cites is kept | `integration/test_fix_platform_evidence_pruning.py::test_repeated_edit_and_confirm_keeps_only_the_current_version`, `integration/test_fix_platform_evidence_pruning.py::test_a_version_that_a_stored_draft_cites_is_kept`, `integration/test_fix_platform_evidence_pruning.py::test_another_visitors_evidence_is_never_pruned` |
| EM-10 | Library request lines do not reach the JSON log | `unit/test_fix_platform_logging.py::test_library_info_lines_do_not_reach_the_json_handler`, `unit/test_fix_platform_logging.py::test_the_http_library_the_openai_sdk_uses_is_silenced` |
| SPEC-05, SPEC-06 | `/readyz` reports the limits; a placeholder key counts as not configured | `integration/test_fix_platform_readyz_limits.py::test_readyz_reports_the_configured_limits_without_a_session`, `unit/test_fix_platform_config.py::test_a_copied_env_example_does_not_report_a_configured_provider`, `unit/test_fix_platform_config.py::test_production_refuses_the_placeholder_key` |
| Configuration | Production needs an explicit database URI; defaults follow the real-provider measurements; a test run cannot reach a hosted database | `unit/test_fix_platform_config.py::test_production_requires_an_explicit_database_uri`, `unit/test_fix_platform_config.py::test_defaults_follow_the_real_provider_measurements`, `unit/test_fix_platform_config.py::test_blueprint_mirrors_the_deployed_values`, `unit/test_fix_lead_test_database_guard.py::test_test_mode_refuses_a_hosted_database_without_echoing_the_uri` |

### Found by the integration pass and the second real evaluation run

| What is checked | Test |
|---|---|
| A whole labelled line returned as one skill ("Backend & Cloud: Python, Flask.") is stored as the skills it lists; a qualifying label and a single item stay whole; an item that is not in the source is still left out | `unit/test_fix_integrator_skill_lines.py::test_labelled_line_is_split_into_the_skills_it_lists`, `unit/test_fix_integrator_skill_lines.py::test_ordinary_and_qualified_skills_stay_whole`, `unit/test_fix_integrator_skill_lines.py::test_lines_returned_as_skills_become_individual_skills_of_the_record`, `unit/test_fix_integrator_skill_lines.py::test_a_standalone_group_of_such_lines_is_split_too_and_counts_as_captured`, `unit/test_fix_integrator_skill_lines.py::test_an_item_that_is_not_in_the_source_is_still_left_out` |
| Coverage: another common name of the same thing answers a keyword ("Google Cloud Run" for "GCP", "Kubernetes" for "k8s"); "cloud" alone does not | `unit/test_fix_coverage_phrases.py::test_a_product_of_the_named_cloud_answers_its_abbreviation`, `unit/test_fix_coverage_phrases.py::test_short_and_long_names_of_one_technology_answer_each_other` |
| Demo mode never prints a first-person remark as a resume bullet | `unit/test_generation_fake_ops.py::test_first_person_remark_is_never_printed_as_a_bullet` |

## Known limits of what these tests show

- The deterministic checks compare words and figures. A sentence can reuse the
  right words and still say something the evidence does not; the tests show
  the documented rules hold, not that fabrication is impossible.
- MongoDB's TTL deletion itself is not waited for (it runs about once a
  minute). The tests check that the TTL indexes exist and that the application
  refuses expired sessions on its own.
- "Restart" is a second application instance with its own database client in
  the same test process, not a killed and restarted operating-system process.
- The real OpenAI provider is exercised only through a stubbed SDK object here.
  The opt-in smoke test (`tests/smoke/README.md`) and the experience master
  evaluation (`docs/EXPERIENCE_MASTER_REPORT.md`) cover the real provider.

---

This matrix was prepared with AI assistance (Claude) during the backend
integration pass. Test names and results can be checked with the commands above.
