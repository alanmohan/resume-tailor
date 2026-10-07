> AI-generated documentation (Claude Code, 2026-10-07). Reviewed and owned by the repository author.

# Experience master evaluation: results before and after the review fixes

This is the report asked for in section 9 of `IMPLEMENTATION_SPEC.md`: the author's own experience master file was run through the real application and the real AI provider against three job postings taken from public careers pages.

**Privacy.** This file holds aggregate numbers, pass/fail results and text from the public postings only. It contains no name, employer, contact detail or sentence from the master file. The master file and the script's own detailed report (`backend/scripts/out/`) are git-ignored. The raw transcripts of the runs were kept outside the repository and deleted afterwards.

## 1. What was run

Date: 2026-10-07 (times in US Eastern). All runs used the application in-process (`APP_ENV=test`, `AI_PROVIDER=openai`) against a disposable local MongoDB database that was dropped afterwards.

| | Run 1, before the fixes | Run 2, after the fixes | Follow-up, after two more fixes |
|---|---|---|---|
| Time | 18:39 to 18:42 | 19:34 to 19:35 | 19:41 and 19:47 to 19:48 |
| Command (from `backend/`) | `EXPERIENCE_MASTER_PATH="../Experience Master.docx" .venv/bin/python -m scripts.evaluate_experience_master` | the same | two throwaway scripts: one ingest; then one ingest, confirm, job analysis and generation for the stretch posting |
| Prompt version | `2026-10-07.6` | `2026-10-07.8` | `2026-10-07.9` |
| Model calls / embedding requests | 10 / 4 | 7 / 4 | 4 / 2 |
| Postings | all three | all three | none, then the stretch posting only |

Both full runs also set `MASTER_EVAL_TRANSCRIPT_DIR` to a folder outside the repository.

Models: generation `gpt-6-luna` with reasoning effort `low`; embeddings `text-embedding-3-small` (1536 dimensions). Retrieval: 4 candidates per requirement, at most 18 evidence records, token budget 6,000. Provider timeout 120 s in run 1 and 180 s afterwards; the semantic verifier was off. No SDK retry and no provider error occurred in any run.

Input: one `.docx` file of 16,651 characters (2,137 words), submitted as a single source. It has four roles, two publications and one project, technology lists under most of them, and no name, contact block or education section.

The follow-up exists because run 2 exposed a new extraction problem (section 3). The evaluation budget allowed one full run after the fixes, so the two fixes made after run 2 were checked with four single model calls rather than a third full run. **The per-job numbers for the close and partial postings are therefore from run 2 only.**

## 2. The postings

All three are live public postings retrieved on 2026-10-07 (`backend/fixtures/jobs/live/`). A fourth posting in that folder (Glean, also a partial fit) was not run.

| Fit category | Company | Title | Public URL |
|---|---|---|---|
| close | Cresta | Machine Learning Engineering Intern | <https://job-boards.greenhouse.io/cresta/jobs/4123863008> |
| partial | Stripe | Software Engineer, New Grad | <https://stripe.com/jobs/search?gh_jid=8128744> |
| stretch | Motional | Senior Software Engineer, ML Infrastructure | <https://motional.com/open-positions/?gh_jid=7869244003#/7869244003> |

The fit categories were assigned when the postings were selected, before any run.

## 3. Extraction

| Measure | Run 1 | Run 2 | Follow-up (same result in both extractions) |
|---|---:|---:|---:|
| Records | 7 | 8 | 7 |
| of which employment / publication / project / skill group | 4 / 2 / 1 / 0 | 4 / 2 / 1 / 1 | 4 / 2 / 1 / 0 |
| Statements (bullets) | 19 | 28 | 28 |
| Records with a summary paragraph | 5 | 5 | 5 |
| Skill entries | 107 | 19 | 107 |
| Skill entries that are a whole labelled line | 0 | 13 | 0 |
| Records and statements whose source span equals the original text slice | 26 of 26 | 36 of 36 | not measured |
| Header fields found verbatim in the master | 26 of 26 | 27 of 27 | not measured |
| Summary paragraphs found verbatim | 5 of 5 | 5 of 5 | not measured |
| Records / statements flagged for review | 0 / 0 | 1 / 0 | 0 / 0 |
| Conflicts | 0 | 0 | 0 |
| Evidence records after confirmation | 32 | 49 | 53 (second extraction; the first was not confirmed) |
| Lines the application itself reports as not captured | no such check | 12 | 9 |
| Notices for a missing name, contact details and education | no such check | 3 of 3 | 3 of 3 |

What changed:

- **The result lists of both publications are now extracted.** Run 1 returned no bullet for either publication; run 2 and the follow-up return four for each (three results and the status line). The project's role line is kept as well. That accounts for the nine additional statements.
- **Run 2 lost most of the skills.** The master file lists technologies under each record as a small section with labelled lines. In run 1 the model stored them as 107 individual skills under their records. In run 2 it made one separate skill group out of those sections and returned each labelled line as a single "skill": 13 such lines were stored, one was dropped for being longer than 120 characters, and the resumes' skills lines showed whole lines. Two fixes followed: the extraction prompt now says that such a section belongs to its record and that every entry is one skill name, and the server splits a labelled line into the skills it lists if a model returns one anyway. In the follow-up both extractions again gave 107 skills under six records, none of them a labelled line.
- The application now reports missing basics. A profile without a name, contact details or education gets one notice for each; run 1 produced the same incomplete resumes without any remark.

## 4. Generation, per posting

Run 1 and run 2 are the two full runs. The last column is the single follow-up generation.

### Job analysis and draft

| Measure | Close R1 | Close R2 | Partial R1 | Partial R2 | Stretch R1 | Stretch R2 | Stretch follow-up |
|---|---:|---:|---:|---:|---:|---:|---:|
| Requirements | 11 | 10 | 18 | 12 | 13 | 8 | 9 |
| of which marked inferred | 0 | 4 | 0 | 0 | 0 | 0 | 1 |
| Model calls for the draft | 2 | 1 | 2 | 1 | 2 | 1 | 1 |
| Correction pass | used | not used | used | not used | used | not used | not used |
| Statements kept | 28 | 24 | 31 | 28 | 25 | 22 | not counted |
| supported | 26 | 22 | 29 | 26 | 23 | 20 | not counted |
| needs_review | 0 | 2 | 0 | 1 | 0 | 0 | 0 |
| not_applicable (greeting, closing) | 2 | 0 | 2 | 1 | 2 | 2 | 1 |
| Omitted claims | 0 | 0 | 1 | 0 | 2 | 0 | 0 |
| Experience and project bullets | 14 | 14 | 16 | 13 | 12 | 9 | 11 |
| Roles shown without a bullet | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Skills listed | 9 | 5 | 10 | 9 | 8 | 7 | 10 |
| Resume words | 369 | 439 | 464 | 409 | 368 | 287 | not counted |
| Cover letter words (paragraphs) | 108 (4) | 220 (4) | 124 (4) | 204 (4) | 102 (4) | 179 (4) | 182 (3) |
| Generation time, seconds | 34.5 | 20.6 | 41.8 | 22.1 | 35.2 | 15.7 | not timed |

All omitted claims in run 1 were skills rejected as "not mentioned in your confirmed profile" although the profile listed them. Run 1's correction passes were triggered by the same message (13 of 14 findings).

### Evidence coverage

`S` supported, `P` partial, `M` missing, `U` uncertain. Percent = (S + 0.5 P) / (S + P + M); uncertain items are left out.

| Posting | Run 1 | Run 2 | Follow-up |
|---|---|---|---|
| Close (Cresta) | 31.2% (S0 P5 M3 U3) | 50.0% (S3 P0 M3 U4) | not run |
| Partial (Stripe) | 50.0% (S2 P7 M2 U7) | 55.6% (S5 P0 M4 U3) | not run |
| Stretch (Motional) | 37.5% (S0 P9 M3 U1) | 14.3% (S1 P0 M6 U1) | 25.0% (S2 P0 M6 U1) |

| Ratings the server changed | Close R1 | Close R2 | Partial R1 | Partial R2 | Stretch R1 | Stretch R2 |
|---|---:|---:|---:|---:|---:|---:|
| Ratings lowered | 4 | 3 | 2 | 1 | 1 | 1 |
| Rationales not shown exactly as the model wrote them | 4 | 4 | 3 | 1 | 2 | 1 |

In run 2 the count of changed rationales includes sentences the server kept and extended with its own reason.

The five ratings lowered in run 2, with the requirement text from the public postings:

| Posting | Requirement | Server's result | Judgement |
|---|---|---|---|
| Close | "Improve reasoning and evaluation in real-world scenarios" | uncertain (the model said partial) | By the new rule: a duty with nothing checkable gets no half credit. The model's own sentence says the evidence does not show it. Defensible. |
| Close | "Scale AI systems for production environments, ensuring performance and reliability across use cases" | uncertain (the model said partial) | Same rule. Defensible. |
| Close | "Research ways to improve security, cost-efficiency, and reliability of AI systems" | uncertain (the model said partial) | Same rule. Defensible. |
| Partial | "Java, Ruby, JavaScript, Scala, and Go" | uncertain | The cited evidence names one of the five languages, and the rule (fewer than half answered) gives "uncertain". "Partial" would also have been reasonable. Borderline. |
| Stretch | "Solid experience with a major cloud provider (AWS, GCP, Azure)" | uncertain | **Not justified.** The cited evidence names a product of one of the three providers, but not the provider's abbreviation as the posting writes it. Fixed afterwards: the provider's name written out now answers its abbreviation ("Google Cloud" for "GCP", "Amazon Web Services" for "AWS"). In the follow-up this requirement is rated supported. |

In run 1 the server lowered 7 of 42 ratings, five of them by comparing single words (for example a list of alternative frameworks of which the evidence showed one, and the hiring company's own name).

### Checks

Every check passed for every posting in both full runs.

| Check | Run 1 | Run 2 |
|---|---|---|
| Fact preservation: every resume header (title, organisation, location, dates) equals the confirmed profile record; every role listed once, in profile order | PASS, PASS, PASS (6, 6, 6 entries) | PASS, PASS, PASS (6, 5, 4 entries) |
| Numeric grounding: every number in a statement occurs in the evidence that statement cites | PASS, PASS, PASS (5, 6, 9 numbers) | PASS, PASS, PASS (10, 3, 0 numbers) |
| Every cited evidence ID opens, and every excerpt with offsets equals that slice of the source | PASS (20/20, 18/18, 18/18) | PASS (18/18, 18/18, 20/20) |
| Coverage summary equals a recount of its items | PASS x3 | PASS x3 |
| Explicit qualifications the profile lacks are not rated supported | PASS (1, 1 and 4 requirements caught) | PASS (1, 1 and 4 requirements caught) |
| No posting technology absent from the master is claimed in a resume or a cited letter paragraph | PASS x3 | PASS x3 |

The stretch posting's "4+ years of professional software engineering experience" was rated partial in run 1 and missing in run 2; "Hands-on experience developing and deploying applications on Kubernetes (k8s)" and the degree requirement were missing in both. Kubernetes, Ray, Go, Java, Ruby, Scala and Azure, which the postings name and the master does not, appear in no resume.

After each full run "Clear my data" removed every document of the session, the token was refused afterwards, and no document was left in the database.

## 5. Latency and tokens

| Step | Run 1 | Run 2 |
|---|---:|---:|
| Ingest (one extraction call) | 23.1 s | 22.0 s |
| Confirm (one embedding request) | 0.9 s | 1.1 s |
| Job analysis, three postings | 8.0 / 11.0 / 7.4 s | 7.1 / 8.2 / 8.3 s |
| Generation, three postings | 34.5 / 41.8 / 35.2 s | 20.6 / 22.1 / 15.7 s |
| Wall time of the whole run | 163.4 s | 105.5 s |
| Input tokens | 54,450 | 33,253 |
| Output tokens | 25,232 | 15,160 |
| Embedding tokens | 3,543 | 4,361 |
| API requests | 14 | 11 |

The follow-up flow of three model calls used 15,688 input and 7,586 output tokens (extraction 6,576 / 4,108, job analysis 2,364 / 1,194, generation 6,748 / 2,284). The token counts of the single extraction call before it were not recorded. Prices were not computed.

The drop between the runs comes from the correction pass: run 1 paid for a second full generation call for every posting, run 2 for none.

## 6. Answers to the questions the fixes were meant to settle

| Question | Run 1 | After the fixes |
|---|---|---|
| Are confirmed skills still rejected? | Yes: 3 skills omitted, and 13 of 14 correction findings were this false message | No. 0 omitted claims in run 2 and in the follow-up. The follow-up is the meaningful case, because there the skills sit under their roles as in run 1: 10 of 10 listed skills supported, four of the cited evidence records being the new skills record of a role or publication |
| Does every generation need a correction pass? | Yes, 3 of 3 | No, 0 of 4 |
| Were the publications' result bullets extracted? | No, 0 of 6 | Yes, 6 of 6 |
| Does the close fit score clearly above the stretch? | No: 31.2% against 37.5% | Yes: 50.0% against 14.3% (25.0% for the stretch in the follow-up). The partial fit, at 55.6%, still scores above the close fit |
| How many ratings did the server lower, and were they justified? | 7 of 42, mostly not | 5 of 30: three defensible, one borderline, one wrong and fixed afterwards |
| How long is the cover letter? | 102 to 124 words | 179 to 220 words |

## 7. Remaining weaknesses

- **One run per configuration.** Model output varies; none of these numbers is an average. The close and partial postings were not run again after the last two fixes.
- **The close fit is not rated above the partial fit.** For the close posting the model rated "Strong understanding of machine learning fundamentals and generative modeling" as missing although the profile has evidence in that field, and three of its duties ended as uncertain. The stricter definitions separate the stretch role well and are harsh on general requirements.
- **The paper most relevant to the close posting is still not used.** One of the two publications is on the posting's own subject. In run 2 none of its evidence was retrieved for this posting and the resume shows the other publication. This was reported after run 1 and is not fixed.
- **Notices about uncaptured text are noisy for this file.** After the last fix every listed skill is stored (107), yet the application still reports nine lines as not captured. The cause was not established; the reported lines were not inspected because the budget for real calls was used up.
- **Cover-letter sentences about the posting are flagged.** In run 2 three of the six greeting and closing paragraphs were marked needs_review because they name something from the posting that the profile does not contain (a team name, "APIs"). The warning names the words one by one, which reads oddly for a two-word team name. Nothing false is claimed, but the user has to acknowledge the flag.
- **Repetition inside one role.** The stretch resume of run 2 has two bullets about the same piece of work and a summary that repeats two bullets almost word for word. The duplicate check only catches near-identical wording.
- **Shorter resumes for the stretch role.** 287 words and 9 bullets in run 2, with neither publication nor the project shown. That is honest for a poor fit, but it is less than a page.
- **No name, contact details or education on any resume.** The master file has none. The application now says so in three notices; it cannot supply them.
- **Years of experience are judged by the model only.** "4+ years" was rated missing in run 2, correctly, but no server rule compares a required number of years with the employment dates.
- **Not exercised here:** the frontend, the deployed service, the fourth posting, and the semantic verifier.
