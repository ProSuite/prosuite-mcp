"""The ProSuite QA Quick Reference, keyed by test descriptor.

The catalog carries prosuite's one-line docstring per test. This adds the
readable description and the family a test belongs to, which is what picking
the right test actually takes. Fetched at runtime rather than vendored, so it
cannot go stale; when it cannot be fetched the catalog is simply what it was.
"""

from __future__ import annotations

import io
import re
import threading
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

_URL = "https://www.dirageosystems.ch/prosuite/doc/ProSuiteQA_QuickReference_en.pdf"
# Short, because this is an enrichment a caller is waiting on: better to lose
# the descriptions than to hold up a condition lookup.
_TIMEOUT_SECONDS = 10

# Prose left, illustrations right. The header carries the family name, and
# repeats on every page of it.
_COLUMN_SPLIT = 430
_HEADER_HEIGHT = 60

# Conditions, transformers and issue filters each announce themselves
# differently, and nothing else in the document uses this shape.
_TEST_LINE = re.compile(r"^(?:Tests?|Transformer|Issue Filter):\s*(.+)$")
_DESCRIPTOR = re.compile(r"^[A-Za-z0-9]+$")
_FOOTER = re.compile(r"^Copyright|^\d+\s*/\s*\d+")


@dataclass(frozen=True)
class QuickRefEntry:
    title: str
    description: str
    category: str
    descriptors: list[str] = field(default_factory=list)


def descriptor_to_method(descriptor: str) -> str:
    """QaMinLength to qa_min_length, which is how prosuite names the factory
    method, minus the arity suffix the Quick Reference does not distinguish."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", descriptor).lower()


def _join(lines: list[str]) -> str:
    """Undo the PDF's line breaks, including hyphenated ones."""
    text = ""
    for line in lines:
        if text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        elif text:
            text = f"{text} {line}"
        else:
            text = line
    return text


def entries_from_lines(lines: list[str], category: str) -> list[QuickRefEntry]:
    """Split one page's prose into entries. Each ends at its Tests: line, whose
    descriptors are the only reliable anchor in the document."""
    entries: list[QuickRefEntry] = []
    buffer: list[str] = []
    for line in lines:
        match = _TEST_LINE.match(line)
        if not match:
            buffer.append(line)
            continue
        descriptors = [
            d.strip() for d in match.group(1).split(",") if _DESCRIPTOR.match(d.strip())
        ]
        if buffer and descriptors:
            entries.append(
                QuickRefEntry(
                    title=buffer[0],
                    description=_join(buffer[1:]),
                    category=category,
                    descriptors=descriptors,
                )
            )
        buffer = []
    return entries


def _page_lines(page: Any) -> tuple[str, list[str]]:
    header = page.crop((0, 0, page.width, _HEADER_HEIGHT)).extract_text() or ""
    category = next((line.strip() for line in header.splitlines() if line.strip()), "")

    body = page.crop((0, _HEADER_HEIGHT, _COLUMN_SPLIT, page.height))
    lines = [
        stripped
        for line in (body.extract_text() or "").splitlines()
        if (stripped := line.strip()) and not _FOOTER.match(stripped)
    ]
    if not category and lines:
        # The last section has no running header and names itself instead.
        category = lines.pop(0)
    return category, lines


def parse(data: bytes) -> dict[str, QuickRefEntry]:
    """Map each documented test to its entry, keyed the way the catalog names
    it. A test named by several descriptors is reachable under each."""
    import pdfplumber

    found: dict[str, QuickRefEntry] = {}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            category, lines = _page_lines(page)
            if not category:
                continue
            for entry in entries_from_lines(lines, category):
                for descriptor in entry.descriptors:
                    found.setdefault(descriptor_to_method(descriptor), entry)
    return found


def _fetch() -> bytes:
    with urllib.request.urlopen(_URL, timeout=_TIMEOUT_SECONDS) as response:
        return response.read()


@lru_cache(maxsize=1)
def load() -> dict[str, QuickRefEntry]:
    """Fetched and parsed once per process.

    Fails soft: the Quick Reference enriches the catalog and does not gate it,
    so an air-gapped host or a moved URL costs the descriptions, not the tools.
    """
    try:
        return parse(_fetch())
    except Exception:
        return {}


def warm() -> None:
    """Start loading now, so the first condition lookup does not wait.

    Downloading and parsing the document takes a couple of seconds, and a
    caller would otherwise pay all of it. Daemon, because a slow fetch must
    not hold the process open.
    """
    threading.Thread(target=load, daemon=True).start()


def for_condition(method_name: str) -> QuickRefEntry | None:
    """The entry for a catalog method, whose arity suffix is ours, not the
    Quick Reference's: qa_min_length_1 and qa_min_length_0 share an entry."""
    return load().get(re.sub(r"_\d+$", "", method_name))
