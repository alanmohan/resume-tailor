"""EM-06: a prose summary becomes several focused evidence records.

Summaries of 160-232 words used to be stored as one record each, because text
was only cut at blank lines or after 250 words. One record then mixed several
unrelated pieces of work, and opening a citation showed the whole description.
Text pasted from a word processor has one line per paragraph, so a single
line break is a boundary too. All content here is fictional.
"""

from app.schemas.common import utc_now
from app.schemas.documents import EvidenceDoc
from app.schemas.profiles import ProfileBullet, ProfileRecord
from app.services.indexing import SUMMARY_MAX_WORDS, SUMMARY_MIN_WORDS, build_evidence
from app.services.ingestion import prepare_source
from app.services.textutil import chunk_text, count_words
from tests.factories import make_profile, make_source

OWNER = "owner-1"
HEADER = "Research Engineer - Tidewater Robotics (2021 - 2024)"
CONTEXT = "[Research Engineer at Tidewater Robotics] "


def sentence(topic: str, number: int) -> str:
    """One 12-word sentence about ``topic``."""
    return f"Step {number} of the {topic} work was planned, built, measured and documented."


def paragraph(topic: str, sentences: int) -> str:
    return " ".join(sentence(topic, number) for number in range(1, sentences + 1))


# Three paragraphs of 72, 60 and 84 words, one line each, as pasted from Word.
PARAGRAPHS = [paragraph("gripper", 6), paragraph("simulator", 5), paragraph("dataset", 7)]
SUMMARY = "\n".join(PARAGRAPHS)


def build(record: ProfileRecord, text: str | None = None) -> list[EvidenceDoc]:
    sources = [make_source(OWNER, text=text)] if text else []
    if text:
        record.source_ref = prepare_source("S1", sources[0]).locate(HEADER)
    return build_evidence(
        make_profile(OWNER, records=[record]),
        sources,
        embedding_model="fake-embedding-256",
        embedding_dimension=256,
        now=utc_now(),
    )


def role(**fields: object) -> ProfileRecord:
    return ProfileRecord(
        record_id="r1",
        category="employment",
        title="Research Engineer",
        organization="Tidewater Robotics",
        **fields,
    )


# ---- chunk_text ---------------------------------------------------------------------


def test_single_line_breaks_separate_chunks_when_asked_to() -> None:
    chunks = chunk_text(SUMMARY, 40, 120, split_at_lines=True)

    assert [chunk.text for chunk in chunks] == PARAGRAPHS
    assert all(SUMMARY[chunk.start : chunk.end] == chunk.text for chunk in chunks)


def test_without_that_option_only_blank_lines_and_the_size_limit_separate_chunks() -> None:
    chunks = chunk_text(SUMMARY, 40, 120)

    # The first chunk runs across the line break and ends in the middle of
    # the second paragraph: this is what mixed unrelated work in one record.
    assert [count_words(chunk.text) for chunk in chunks] == [120, 96]
    assert "\n" in chunks[0].text


def test_one_long_paragraph_is_cut_into_groups_of_whole_sentences() -> None:
    text = paragraph("pipeline", 19)  # 228 words on one line

    chunks = chunk_text(text, 40, 120, split_at_lines=True)

    assert [count_words(chunk.text) for chunk in chunks] == [120, 108]
    assert all(chunk.text.endswith("documented.") for chunk in chunks)
    assert " ".join(chunk.text for chunk in chunks) == text


def test_short_lines_are_gathered_until_a_chunk_is_worth_citing() -> None:
    text = "\n".join(sentence("audit", number) for number in range(1, 13))  # 12 lines, 144 words

    chunks = chunk_text(text, 40, 120, split_at_lines=True)

    # Four 12-word lines reach the 40-word minimum; a line is never split.
    assert [count_words(chunk.text) for chunk in chunks] == [48, 48, 48]


def test_text_within_the_limit_stays_one_chunk_whatever_its_line_breaks() -> None:
    text = "\n".join(PARAGRAPHS[:2])  # 132 words: above 120, so it is split
    short = "\n".join([sentence("review", 1), sentence("review", 2)])  # 24 words

    assert len(chunk_text(text, 40, 120, split_at_lines=True)) == 2
    assert [chunk.text for chunk in chunk_text(short, 40, 120, split_at_lines=True)] == [short]


# ---- evidence built from a summary --------------------------------------------------


def test_each_paragraph_of_a_long_summary_is_its_own_evidence_record() -> None:
    text = f"Experience\n{HEADER}\n{SUMMARY}\n- Mentored two interns\n"
    record = role(
        summary=SUMMARY,
        bullets=[
            ProfileBullet(bullet_id="b1", text="Mentored two interns", provenance="extracted")
        ],
        provenance="extracted",
    )

    evidence = build(record, text)
    summary_records = evidence[1:-1]

    assert [item.text for item in summary_records] == [CONTEXT + part for part in PARAGRAPHS]
    for item, part in zip(summary_records, PARAGRAPHS, strict=True):
        assert SUMMARY_MIN_WORDS <= count_words(part) <= SUMMARY_MAX_WORDS
        # Opening the citation shows this passage only, at its exact place.
        assert item.excerpt == part
        assert text[item.source.start : item.source.end] == part
        assert (item.record_id, item.bullet_id, item.category) == ("r1", None, "employment")
        assert item.parent.title == "Research Engineer"
    spans = [(item.source.start, item.source.end) for item in summary_records]
    assert spans == sorted(spans)


def test_a_user_written_summary_is_split_the_same_way() -> None:
    evidence = build(role(summary=SUMMARY, provenance="user_added"))

    assert [item.excerpt for item in evidence[1:]] == PARAGRAPHS
    assert all(item.text.startswith(CONTEXT) for item in evidence[1:])


def test_a_short_summary_stays_one_record() -> None:
    summary = "\n".join(PARAGRAPHS[:1] + [sentence("handover", 1)])  # 84 words, two lines

    evidence = build(role(summary=summary, provenance="user_added"))

    assert [item.excerpt for item in evidence[1:]] == [summary]


def test_a_bullet_of_the_same_length_is_not_split() -> None:
    """A bullet is one statement; only running prose is cut finely."""
    statement = paragraph("gripper", 17)  # 204 words
    bullet = ProfileBullet(bullet_id="b1", text=statement, provenance="user_added")

    evidence = build(role(bullets=[bullet], provenance="user_added"))

    assert [item.excerpt for item in evidence[1:]] == [statement]
