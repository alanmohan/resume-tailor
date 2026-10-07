"""Text helpers: normalisation with offsets, quote location, tokens, chunking."""

import hashlib

import pytest

from app.services.textutil import (
    chunk_text,
    content_hash,
    count_words,
    estimate_tokens,
    keyword_overlap,
    locate_quote,
    normalize_whitespace,
    split_paragraphs,
    split_sentences,
    strip_contact_details,
    tokenize,
)

RESUME = (
    "  Jordan Rivera\r\n"
    "Senior   Data\tEngineer – Northwind Labs\r\n"
    "\r\n\r\n\r\n"
    "• Cut pipeline  runtime by 40%\n"
    "• Mentored 3 junior engineers  \n\n"
)


def locate(original: str, quote: str, search_from: int = 0) -> tuple[int, int] | None:
    normalized = normalize_whitespace(original)
    return locate_quote(original, normalized.text, normalized.offsets, quote, search_from)


# ---- normalize_whitespace ----------------------------------------------------------


def test_normalisation_collapses_whitespace_but_keeps_line_structure() -> None:
    normalized = normalize_whitespace(RESUME)
    assert normalized.text == (
        "Jordan Rivera\n"
        "Senior Data Engineer – Northwind Labs\n\n"
        "• Cut pipeline runtime by 40%\n"
        "• Mentored 3 junior engineers"
    )


def test_every_normalised_character_maps_back_to_its_original_position() -> None:
    normalized = normalize_whitespace(RESUME)
    assert len(normalized.offsets) == len(normalized.text)
    assert normalized.offsets == sorted(normalized.offsets)
    for position, char in enumerate(normalized.text):
        if not char.isspace():
            assert RESUME[normalized.offsets[position]] == char


def test_span_mapping_returns_the_original_slice() -> None:
    normalized = normalize_whitespace(RESUME)
    start = normalized.text.index("Senior Data Engineer")
    original_start, original_end = normalized.to_original_span(
        start, start + len("Senior Data Engineer")
    )
    assert RESUME[original_start:original_end] == "Senior   Data\tEngineer"


@pytest.mark.parametrize("text", ["", "   ", "\n\n\t \r\n"])
def test_blank_text_normalises_to_empty(text: str) -> None:
    normalized = normalize_whitespace(text)
    assert normalized.text == ""
    assert normalized.offsets == []


def test_zero_width_characters_are_treated_as_whitespace() -> None:
    assert normalize_whitespace("data​base﻿").text == "data base"


# ---- locate_quote ------------------------------------------------------------------


def test_exact_quote_is_located_in_the_original_text() -> None:
    span = locate(RESUME, "Cut pipeline runtime by 40%")
    assert span is not None
    assert RESUME[span[0] : span[1]] == "Cut pipeline  runtime by 40%"


def test_quote_spanning_a_line_break_is_located() -> None:
    span = locate(RESUME, "Jordan Rivera\nSenior Data Engineer")
    assert span is not None
    assert RESUME[span[0] : span[1]] == "Jordan Rivera\r\nSenior   Data\tEngineer"


def test_tolerant_match_ignores_case_dash_style_and_line_breaks() -> None:
    # ASCII hyphen instead of the en dash, different case, space instead of newline.
    span = locate(RESUME, "jordan rivera senior data engineer - northwind labs")
    assert span is not None
    assert RESUME[span[0] : span[1]] == (
        "Jordan Rivera\r\nSenior   Data\tEngineer – Northwind Labs"
    )


def test_tolerant_match_handles_curly_quotes_and_ellipsis() -> None:
    original = "Won the “Best Tool” award… twice"
    span = locate(original, 'the "Best Tool" award... twice')
    assert span is not None
    assert original[span[0] : span[1]] == "the “Best Tool” award… twice"


def test_a_quote_that_is_not_in_the_source_is_not_located() -> None:
    assert locate(RESUME, "Cut pipeline runtime by 60%") is None
    assert locate(RESUME, "Led a team of 12 engineers") is None


@pytest.mark.parametrize("quote", ["", " ", "\n", "x"])
def test_empty_or_tiny_quotes_are_rejected(quote: str) -> None:
    assert locate("x marks the spot", quote) is None


def test_search_from_selects_the_later_occurrence() -> None:
    original = "Acme Corp\n- Led a team of 5\n\nGlobex\n- Led a team of 5\n"
    first = locate(original, "Led a team of 5")
    second = locate(original, "Led a team of 5", search_from=original.index("Globex"))
    assert first is not None and second is not None
    assert first[0] < original.index("Globex") < second[0]
    assert original[second[0] : second[1]] == "Led a team of 5"


def test_search_from_falls_back_to_an_earlier_occurrence() -> None:
    original = "Built the billing service.\nLater moved to platform work."
    span = locate(original, "billing service", search_from=len(original) - 5)
    assert span is not None
    assert original[span[0] : span[1]] == "billing service"


# ---- tokenize / keyword_overlap ----------------------------------------------------


def test_tokenize_lowercases_and_drops_stopwords() -> None:
    assert tokenize("Led the migration of a Payments API to the cloud.") == [
        "led",
        "migration",
        "payments",
        "api",
        "cloud",
    ]


def test_tokenize_keeps_technology_names_intact() -> None:
    tokens = tokenize("C++, C#, Node.js, CI/CD and scikit-learn (Python 3.12).")
    assert tokens == ["c++", "c#", "node.js", "ci", "cd", "scikit", "learn", "python", "3.12"]


def test_tokenize_handles_empty_and_symbol_only_text() -> None:
    assert tokenize("") == []
    assert tokenize("... --- !!!") == []


def test_keyword_overlap_is_the_fraction_of_query_tokens_found() -> None:
    query = set(tokenize("Python and Docker experience"))
    text = set(tokenize("Built Python services and deployed them with Docker"))
    assert query == {"python", "docker", "experience"}
    assert keyword_overlap(query, text) == pytest.approx(2 / 3)


def test_keyword_overlap_bounds() -> None:
    assert keyword_overlap(set(), {"python"}) == 0.0
    assert keyword_overlap({"kubernetes"}, {"docker"}) == 0.0
    assert keyword_overlap({"docker"}, {"docker", "python"}) == 1.0


# ---- strip_contact_details ---------------------------------------------------------


def test_contact_details_are_removed() -> None:
    text = (
        "Jordan Rivera | jordan.rivera+jobs@example.com | (412) 555-0142 | "
        "https://example.com/portfolio | linkedin.com/in/jordan-rivera | Pittsburgh"
    )
    stripped = strip_contact_details(text)
    assert "@" not in stripped
    assert "555" not in stripped
    assert "example.com" not in stripped
    assert "linkedin.com" not in stripped
    assert "Jordan Rivera" in stripped
    assert "Pittsburgh" in stripped


@pytest.mark.parametrize(
    "phone", ["+1 412-555-0142", "412.555.0142", "+44 20 7946 0958", "4125550142"]
)
def test_common_phone_formats_are_removed(phone: str) -> None:
    assert strip_contact_details(f"Call {phone} today") == "Call today"


@pytest.mark.parametrize(
    "text",
    [
        "Software Engineer, 2019-2021",
        "Jan 2020 - Mar 2023",
        "Processed 1,000,000 events and saved $250,000",
        "Reduced latency by 40% across 12 services",
        "Version 3.12.2 of Python",
    ],
)
def test_dates_and_metrics_are_left_alone(text: str) -> None:
    assert strip_contact_details(text) == text


# ---- estimate_tokens / content_hash ------------------------------------------------


def test_estimate_tokens_is_about_four_characters_per_token() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("x" * 400) == 100


def test_content_hash_is_sha256_of_the_text() -> None:
    assert content_hash("same text") == hashlib.sha256(b"same text").hexdigest()
    assert content_hash("same text") == content_hash("same text")
    assert content_hash("same text") != content_hash("same text.")


# ---- splitting and chunking --------------------------------------------------------


def test_split_paragraphs_returns_trimmed_spans_with_offsets() -> None:
    text = "  First paragraph.\nStill first.\n\n\nSecond paragraph.  \n \nThird."
    paragraphs = split_paragraphs(text)
    assert [p.text for p in paragraphs] == [
        "First paragraph.\nStill first.",
        "Second paragraph.",
        "Third.",
    ]
    for paragraph in paragraphs:
        assert text[paragraph.start : paragraph.end] == paragraph.text


def test_split_sentences_splits_on_punctuation_and_line_breaks() -> None:
    text = "Built an API. Deployed it to AWS!\nLed 3 engineers\nCut cost by approx. five percent."
    sentences = split_sentences(text)
    assert [s.text for s in sentences] == [
        "Built an API.",
        "Deployed it to AWS!",
        "Led 3 engineers",
        "Cut cost by approx. five percent.",
    ]
    for sentence in sentences:
        assert text[sentence.start : sentence.end] == sentence.text


def test_short_text_is_a_single_chunk() -> None:
    text = "  Reduced build time by 30% by caching dependencies.  "
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].text == text.strip()
    assert text[chunks[0].start : chunks[0].end] == chunks[0].text


def test_blank_text_gives_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text("  \n ") == []


def test_long_text_is_split_at_sentence_boundaries_within_the_word_limits() -> None:
    sentences = [
        f"Sentence number {index} describes one measurable result of the project."
        for index in range(60)
    ]
    text = " ".join(sentences)
    chunks = chunk_text(text, min_words=100, max_words=250)

    assert len(chunks) > 1
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
        assert count_words(chunk.text) <= 250
        assert chunk.text.startswith("Sentence number")
        assert chunk.text.endswith(".")
    # Nothing is lost and nothing overlaps.
    assert sum(count_words(chunk.text) for chunk in chunks) == count_words(text)
    for previous, following in zip(chunks, chunks[1:], strict=False):
        assert previous.end <= following.start


def test_chunks_prefer_paragraph_boundaries_once_large_enough() -> None:
    paragraph = " ".join(["word"] * 120) + "."
    text = f"{paragraph}\n\n{paragraph}\n\n{paragraph}"
    chunks = chunk_text(text, min_words=100, max_words=250)
    assert [count_words(chunk.text) for chunk in chunks] == [120, 120, 120]


def test_a_single_overlong_sentence_is_cut_by_word_count() -> None:
    text = " ".join(f"w{index}" for index in range(600))
    chunks = chunk_text(text, min_words=100, max_words=250)
    assert [count_words(chunk.text) for chunk in chunks] == [250, 250, 100]
    assert chunks[0].text.startswith("w0 ")
    assert chunks[-1].text.endswith("w599")
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
