"""Pure text helpers shared by ingestion, retrieval and validation.

Nothing here touches the database or a provider, so everything is unit tested
directly (tests/unit/test_textutil.py).
"""

import bisect
import hashlib
import re
from dataclasses import dataclass

# ---- Whitespace normalisation with an offset map -----------------------------------

_NEWLINES = frozenset("\n\r\x0b\x0c  ")
_ZERO_WIDTH = frozenset("​‌‍﻿")


@dataclass(frozen=True)
class NormalizedText:
    """Whitespace-normalised text plus, for every character, its position in
    the original: ``offsets[i]`` is the original index of ``text[i]``."""

    text: str
    offsets: list[int]

    def to_original_span(self, start: int, end: int) -> tuple[int, int]:
        """Map the half-open range [start, end) of the normalised text to the
        matching half-open range of the original text."""
        return self.offsets[start], self.offsets[end - 1] + 1


def normalize_whitespace(original: str) -> NormalizedText:
    """Collapse whitespace while remembering where each character came from.

    Rules: runs of spaces/tabs (including non-breaking and zero-width spaces)
    become one space; a line break stays one "\\n"; two or more line breaks
    become a paragraph break "\\n\\n"; leading and trailing whitespace is
    dropped. Non-whitespace characters are never changed, so a quote copied
    from the normalised text can be mapped back to exact original offsets.
    """
    chars: list[str] = []
    offsets: list[int] = []
    gap_start: int | None = None  # original index where the current whitespace run began
    gap_newlines = 0

    for index, char in enumerate(original):
        if char in _NEWLINES:
            is_crlf_first_half = (
                char == "\r" and index + 1 < len(original) and original[index + 1] == "\n"
            )
            if not is_crlf_first_half:
                gap_newlines += 1
            if gap_start is None:
                gap_start = index
            continue
        if char.isspace() or char in _ZERO_WIDTH:
            if gap_start is None:
                gap_start = index
            continue

        if gap_start is not None and chars:
            separator = "\n\n" if gap_newlines >= 2 else "\n" if gap_newlines == 1 else " "
            chars.extend(separator)
            offsets.extend([gap_start] * len(separator))
        gap_start = None
        gap_newlines = 0
        chars.append(char)
        offsets.append(index)

    return NormalizedText("".join(chars), offsets)


# ---- Locating model-supplied quotes in the original text ---------------------------

MIN_QUOTE_CHARS = 2

# Typographic characters a model commonly swaps for their ASCII look-alikes.
_FOLD_MAP = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "−": "-",
    "…": "...",
}


def _fold(text: str) -> NormalizedText:
    """Aggressive normalisation for tolerant matching: lower case, ASCII quotes
    and dashes, and every whitespace run (line breaks included) as one space."""
    chars: list[str] = []
    offsets: list[int] = []
    pending_space_at: int | None = None
    for index, char in enumerate(text):
        if char.isspace() or char in _ZERO_WIDTH:
            if pending_space_at is None:
                pending_space_at = index
            continue
        if pending_space_at is not None and chars:
            chars.append(" ")
            offsets.append(pending_space_at)
        pending_space_at = None
        lowered = char.lower()
        # A few characters lower-case to two characters; keep those unchanged so
        # one input character never shifts the offsets of what follows.
        replacement = _FOLD_MAP.get(char, lowered if len(lowered) == 1 else char)
        chars.extend(replacement)
        offsets.extend([index] * len(replacement))
    return NormalizedText("".join(chars), offsets)


def _find_from(haystack: NormalizedText, needle: str, original_from: int) -> int:
    """Index of ``needle`` in the haystack, preferring a match at or after the
    original offset ``original_from`` and falling back to the first match."""
    start_at = bisect.bisect_left(haystack.offsets, original_from)
    position = haystack.text.find(needle, start_at)
    return position if position != -1 else haystack.text.find(needle)


def locate_quote(
    original: str, normalized: str, offset_map: list[int], quote: str, search_from: int = 0
) -> tuple[int, int] | None:
    """Find a verbatim quote returned by the model and return its ``(start, end)``
    offsets in the ORIGINAL text, or None when it cannot be found.

    The model sees the normalised text and is asked for exact quotes, never
    offsets (models miscount characters). Matching is tried in two steps:

    1. exact match in the normalised text (after normalising the quote's own
       whitespace the same way);
    2. tolerant match that ignores letter case, straight/curly quote and dash
       style, and line breaks versus spaces.

    None means the quote is not in the source: the caller must flag the item
    for review ("source span not found") rather than accept it.

    ``search_from`` (an original offset) prefers a match at or after that
    position, which picks the right occurrence when the same words appear
    twice, e.g. the same bullet under two roles.
    """
    exact_needle = normalize_whitespace(quote).text
    if len(exact_needle) < MIN_QUOTE_CHARS:
        return None

    exact_haystack = NormalizedText(normalized, offset_map)
    position = _find_from(exact_haystack, exact_needle, search_from)
    if position != -1:
        return exact_haystack.to_original_span(position, position + len(exact_needle))

    folded_haystack = _fold(original)
    folded_needle = _fold(quote).text
    position = _find_from(folded_haystack, folded_needle, search_from)
    if position != -1:
        return folded_haystack.to_original_span(position, position + len(folded_needle))
    return None


# ---- Tokens and keyword overlap ----------------------------------------------------

# Letters and digits plus the symbols that are part of technology names
# ("c++", "c#", "node.js"). Slashes and hyphens split, so "CI/CD" gives "ci", "cd".
_TOKEN_PATTERN = re.compile(r"(?:[^\W_]|[+#.])+")

STOPWORDS = frozenset(
    """
    a about all also an and any are as at be been being but by can could do does
    e.g etc for from had has have i i.e if in into is it its more must my no not of
    on or other our own should so such than that the their them then there these
    they this those to up us was we were what when which who will with within
    would you your
    """.split()
)


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens without stopwords, in order of appearance."""
    tokens = []
    for match in _TOKEN_PATTERN.finditer(text.lower()):
        token = match.group().strip(".")
        if token and token not in STOPWORDS:
            tokens.append(token)
    return tokens


def keyword_overlap(query_tokens: set[str], text_tokens: set[str]) -> float:
    """Fraction (0.0-1.0) of the distinct query tokens that occur in the text.
    Callers pass ``set(tokenize(...))``; an empty query scores 0."""
    if not query_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)


# ---- Embedding input hygiene -------------------------------------------------------

_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_URL_PATTERN = re.compile(
    r"(?:https?://|www\.)\S+|\b(?:linkedin\.com|github\.com)/\S+", re.IGNORECASE
)
# Either an international number starting with "+", or a 3-3-4 digit North
# American number. Deliberately narrow so year ranges such as "2019-2021" and
# large figures such as "1,000,000" are left alone.
_PHONE_PATTERN = re.compile(
    r"(?<![\w.])(?:\+\d[\d\s().-]{7,}\d|(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})(?!\w)"
)


def strip_contact_details(text: str) -> str:
    """Remove e-mail addresses, URLs and phone numbers.

    Used on text before it is embedded: contact details say nothing about a
    skill or achievement, and leaving them out keeps personal identifiers out
    of embedding requests.
    """
    for pattern in (_EMAIL_PATTERN, _URL_PATTERN, _PHONE_PATTERN):
        text = pattern.sub(" ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ---- Sizes and hashes --------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Approximate token count: about four characters per token for English.
    Only used for budgeting context size; real usage comes from the provider."""
    return (len(text) + 3) // 4


def content_hash(text: str) -> str:
    """Hex SHA-256 of the text; the key of the embedding cache."""
    return hashlib.sha256(text.encode()).hexdigest()


def count_words(text: str) -> int:
    return len(text.split())


def fold_text(text: str) -> str:
    """Comparison form of a text: lower case, with every run of whitespace as
    one space. Two texts that differ only in case or spacing fold to the same
    string; used to spot duplicates and to look for a term in a text."""
    return " ".join(text.casefold().split())


# Wording that marks a skill as known from study or in passing only: a skill
# group titled "Coursework exposure only", a note such as "(basic familiarity)".
LIMITED_EXPOSURE = re.compile(
    r"\b(?:course\s?work|exposure|familiar(?:ity)?|beginner|novice|introductory"
    r"|(?:basic|limited|some)\s+(?:knowledge|understanding|experience))\b",
    re.IGNORECASE,
)


# ---- Instruction-like text in untrusted data ---------------------------------------

# Common prompt-injection phrasing: a chat-role prefix at the start of a line,
# "ignore all previous instructions", "instructions for any AI system".
_INSTRUCTION_LIKE = re.compile(
    r"^\s*(?:system|assistant)\s*:"
    r"|\b(?:ignore|disregard|forget)\s+(?:(?:all|any|the|your)\s+)*"
    r"(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|rules|prompts?)\b"
    r"|\binstructions?\s+for\s+any\s+(?:ai|llm|language\s+model|assistant)\b",
    re.IGNORECASE | re.MULTILINE,
)


def looks_like_instruction(text: str) -> bool:
    """True for text that addresses an AI system instead of describing a
    person or a job, such as a line planted in a resume or job posting.

    This is a second line of defence, not the main one: the prompts already
    tell the model that supplied text is data, and generated claims are
    validated against evidence. The check is a heuristic for well-known
    phrasing; it can miss a new wording and can match an innocent sentence, so
    callers leave such text out visibly (with a note) rather than silently.
    """
    return _INSTRUCTION_LIKE.search(text) is not None


def surrounding_lines(text: str, start: int, end: int) -> str:
    """The complete line or lines of ``text`` that contain the range
    [start, end). A quote may be only part of a line; whether the line is an
    instruction can only be judged from the whole line."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    return text[line_start : len(text) if line_end == -1 else line_end]


# ---- Splitting long text into chunks -----------------------------------------------


@dataclass(frozen=True)
class TextSpan:
    """A slice of some text: ``text == source[start:end]``."""

    start: int
    end: int
    text: str


_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n\s*")
_LINE_BREAK = re.compile(r"\s*\n\s*")
# A line break, or whitespace after ., ! or ? when the next character does not
# start with a lower-case letter (so "approx. five" is not split).
_SENTENCE_BREAK = re.compile(r"\s*\n\s*|(?<=[.!?])\s+(?=[^\sa-z])")


def _split_on(source: str, start: int, end: int, boundary: re.Pattern[str]) -> list[TextSpan]:
    """Split ``source[start:end]`` at every ``boundary`` match into trimmed,
    non-empty spans whose offsets refer to ``source``."""
    spans = []
    cursor = start
    cut_points = [(m.start(), m.end()) for m in boundary.finditer(source, start, end)]
    for piece_end, next_start in [*cut_points, (end, end)]:
        piece = source[cursor:piece_end]
        stripped = piece.strip()
        if stripped:
            piece_start = cursor + (len(piece) - len(piece.lstrip()))
            spans.append(TextSpan(piece_start, piece_start + len(stripped), stripped))
        cursor = next_start
    return spans


def split_paragraphs(text: str) -> list[TextSpan]:
    """Paragraphs: blocks separated by one or more blank lines."""
    return _split_on(text, 0, len(text), _PARAGRAPH_BREAK)


def split_sentences(text: str) -> list[TextSpan]:
    """Sentences and separate lines (resume bullets often have no full stop)."""
    return _split_on(text, 0, len(text), _SENTENCE_BREAK)


def split_lines(text: str, start: int = 0, end: int | None = None) -> list[TextSpan]:
    """The non-empty lines of ``text[start:end]``, trimmed; offsets refer to ``text``."""
    return _split_on(text, start, len(text) if end is None else end, _LINE_BREAK)


def _split_by_words(source: str, span: TextSpan, max_words: int) -> list[TextSpan]:
    """Last resort for a single sentence longer than ``max_words``: cut it
    after every ``max_words`` words."""
    words = list(re.finditer(r"\S+", source[span.start : span.end]))
    pieces = []
    for first in range(0, len(words), max_words):
        group = words[first : first + max_words]
        start = span.start + group[0].start()
        end = span.start + group[-1].end()
        pieces.append(TextSpan(start, end, source[start:end]))
    return pieces


def chunk_text(
    text: str, min_words: int = 100, max_words: int = 250, *, split_at_lines: bool = False
) -> list[TextSpan]:
    """Split text into chunks of roughly ``min_words``-``max_words`` words.

    Text of at most ``max_words`` words is returned as one chunk. Longer text
    is cut only at paragraph or sentence boundaries: sentences are packed into
    a chunk until the next one would exceed ``max_words``, and a chunk also
    ends at a paragraph break once it holds ``min_words`` words. Each chunk is
    a contiguous slice of ``text`` (``chunk.text == text[chunk.start:chunk.end]``)
    so the caller can turn it into exact source offsets. Chunks do not overlap;
    the evidence builder adds the role/project context to each one instead.

    A paragraph break is a blank line. With ``split_at_lines`` a single line
    break counts as one too: prose pasted from a word processor has one line
    per paragraph and no blank lines in between.
    """
    whole = text.strip()
    if not whole:
        return []
    if count_words(whole) <= max_words:
        start = len(text) - len(text.lstrip())
        return [TextSpan(start, start + len(whole), whole)]

    chunks: list[TextSpan] = []
    chunk_start: int | None = None
    chunk_end = 0
    chunk_words = 0

    def close_chunk() -> None:
        nonlocal chunk_start, chunk_words
        if chunk_start is not None:
            chunks.append(TextSpan(chunk_start, chunk_end, text[chunk_start:chunk_end]))
        chunk_start = None
        chunk_words = 0

    for paragraph in split_lines(text) if split_at_lines else split_paragraphs(text):
        for sentence in _split_on(text, paragraph.start, paragraph.end, _SENTENCE_BREAK):
            words = count_words(sentence.text)
            if words > max_words:
                close_chunk()
                chunks.extend(_split_by_words(text, sentence, max_words))
                continue
            if chunk_words + words > max_words:
                close_chunk()
            if chunk_start is None:
                chunk_start = sentence.start
            chunk_end = sentence.end
            chunk_words += words
        if chunk_words >= min_words:
            close_chunk()
    close_chunk()
    return chunks
