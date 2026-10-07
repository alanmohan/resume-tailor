"""Download the full text of the live job postings for local-only testing.

Each JSON file in this folder describes one real public posting and records
"fetch_url": the employer's public Greenhouse job-board API address the
posting was read from. This script downloads every posting again, converts its
HTML to plain text and writes it to _local_full/<slug>.txt.

_local_full/ is git-ignored. The full listings belong to the employers, so they
must never be committed or redistributed; only the short excerpts in the JSON
files are kept in the repository.

The script also checks that each excerpt in "selected_requirements" still
appears word for word in the downloaded text, so an edited or closed posting is
noticed instead of silently going stale.

Usage (needs network access, no credentials):
    python backend/fixtures/jobs/live/fetch_live_jobs.py
"""

import html
import json
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

LIVE_DIR = Path(__file__).resolve().parent
LOCAL_FULL_DIR = LIVE_DIR / "_local_full"
REQUEST_TIMEOUT_SECONDS = 30

# Tags that end the current line of text. <li> is handled separately.
BLOCK_TAGS = {"p", "div", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "table", "section"}


class PostingTextParser(HTMLParser):
    """Collect a posting's HTML as text blocks, remembering which are list items."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[bool, str]] = []  # (is_bullet, text)
        self._pieces: list[str] = []
        self._in_list_item = False

    def _close_block(self) -> None:
        text = " ".join("".join(self._pieces).split())
        if text:
            self.blocks.append((self._in_list_item, text))
        self._pieces = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "li":
            self._close_block()
            self._in_list_item = True
        elif tag in BLOCK_TAGS:
            self._close_block()

    def handle_endtag(self, tag: str) -> None:
        if tag == "li":
            self._close_block()
            self._in_list_item = False
        elif tag in BLOCK_TAGS:
            self._close_block()

    def handle_data(self, data: str) -> None:
        self._pieces.append(data)

    def close(self) -> None:
        """Finish parsing and keep any text that follows the last closing tag."""
        super().close()
        self._close_block()


def html_to_text(markup: str) -> str:
    """Render posting HTML as plain text: paragraphs, and "- " bullet lines.

    Consecutive bullets stay on adjacent lines; everything else is separated
    by one blank line, which matches how a person would paste a job posting.
    """
    parser = PostingTextParser()
    parser.feed(markup)
    parser.close()

    lines: list[str] = []
    previous_was_bullet = False
    for is_bullet, text in parser.blocks:
        if lines and not (is_bullet and previous_was_bullet):
            lines.append("")
        lines.append(f"- {text}" if is_bullet else text)
        previous_was_bullet = is_bullet
    return "\n".join(lines) + "\n"


def download_posting_text(fetch_url: str) -> str:
    """Return the plain text of one posting from the public job-board API."""
    with urllib.request.urlopen(fetch_url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        posting = json.load(response)
    # The API returns the description as HTML with its tags entity-escaped.
    return html_to_text(html.unescape(posting["content"]))


def missing_excerpts(fixture: dict, full_text: str) -> list[str]:
    """Return the selected requirement excerpts that are not in the full text."""
    return [item["text"] for item in fixture["selected_requirements"] if item["text"] not in full_text]


def refresh(fixture_path: Path) -> bool:
    """Download one posting; return True when it was saved and still matches."""
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    slug = fixture_path.stem
    try:
        full_text = download_posting_text(fixture["fetch_url"])
    except urllib.error.HTTPError as error:
        print(f"FAILED  {slug}: HTTP {error.code} (the posting may have closed); kept any existing local copy")
        return False
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"FAILED  {slug}: {error}; kept any existing local copy")
        return False

    LOCAL_FULL_DIR.mkdir(exist_ok=True)
    (LOCAL_FULL_DIR / f"{slug}.txt").write_text(full_text, encoding="utf-8")

    changed = missing_excerpts(fixture, full_text)
    if changed:
        print(f"CHANGED {slug}: {len(changed)} recorded excerpt(s) no longer appear in the posting")
        for excerpt in changed:
            print(f"          {excerpt[:90]}")
        return False
    print(f"OK      {slug}: {len(full_text)} characters saved to _local_full/{slug}.txt")
    return True


def main() -> int:
    fixture_paths = sorted(LIVE_DIR.glob("*.json"))
    results = [refresh(path) for path in fixture_paths]
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
