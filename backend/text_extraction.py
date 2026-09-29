"""
text_extraction.py
-------------------
Turns a raw uploaded file (PDF / DOCX / TXT) into clean, chunked text that is
safe to feed into the concept-extraction pipeline.

Responsibilities:
  1. Extract raw text per-page (so we can spot repeating headers/footers).
  2. Strip headers, footers, page numbers, subject-code/faculty metadata
     blocks, roll-no/name blanks, dates, URLs/emails and other non-content
     noise.
  3. Split the cleaned text into paragraph-level "chunks" that are large
     enough to carry real educational content (used later as the context
     window for concept + question generation).

Nothing here tries to understand *meaning* yet - that is concept_extraction's
job. This module's only responsibility is: raw PDF bytes in, clean readable
prose chunks out.
"""

import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

# ---------------------------------------------------------------------------
# Raw extraction
# ---------------------------------------------------------------------------

def extract_pages_from_pdf(file_storage):
    """Returns a list of per-page raw text strings."""
    try:
        import pypdf
    except ImportError:
        return []
    reader = pypdf.PdfReader(file_storage)
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def extract_text_from_docx(file_storage) -> str:
    try:
        import docx
    except ImportError:
        return ""
    document = docx.Document(file_storage)
    return "\n".join(p.text for p in document.paragraphs)


def extract_pages_from_pptx(file_storage):
    """Extract visible text from each slide in a PowerPoint presentation."""
    namespace = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    try:
        with zipfile.ZipFile(file_storage) as presentation:
            slide_paths = sorted(
                (path for path in presentation.namelist()
                 if re.fullmatch(r"ppt/slides/slide\d+\.xml", path)),
                key=lambda path: int(re.search(r"slide(\d+)\.xml$", path).group(1)),
            )
            pages = []
            for path in slide_paths:
                root = ET.fromstring(presentation.read(path))
                paragraphs = []
                for paragraph in root.iter(f"{namespace}p"):
                    text = " ".join(
                        node.text or "" for node in paragraph.iter(f"{namespace}t")
                    ).strip()
                    if text:
                        paragraphs.append(text)
                pages.append("\n".join(paragraphs))
            return pages
    except (zipfile.BadZipFile, ET.ParseError) as error:
        raise ValueError("This PPTX file is invalid or damaged.") from error


# ---------------------------------------------------------------------------
# Noise / metadata detection
# ---------------------------------------------------------------------------

_PAGE_NUM_RE = re.compile(r"^\s*(page\s*)?\d{1,4}\s*(/\s*\d{1,4})?\s*$", re.I)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
_ROLL_NAME_RE = re.compile(r"(roll\s*no\.?|name\s*:|enrollment\s*no)", re.I)
_METADATA_LINE_RE = re.compile(
    r"(subject\s*code|course\s*pre-?requisite|prepared\s*by|faculty|dr\.\s*[A-Z]"
    r"|department\s*of|university|semester|academic\s*year|examination\s*seat"
    r"|invigilator|duration\s*:|total\s*marks\s*:|max(?:imum)?\s*marks)",
    re.I,
)
_BULLET_GLYPH_RE = re.compile(r"^[\u25A0\u25CF\u2022\u2023\u25E6\u2043\u2219\-\*\s]{1,4}$")
_SHORT_ALLCAPS_RE = re.compile(r"^[A-Z0-9 &\-]{2,40}$")


def _is_noise_line(line: str, repeated: set) -> bool:
    s = line.strip()
    if not s:
        return True
    if s in repeated:
        return True
    if _PAGE_NUM_RE.match(s):
        return True
    if _ROLL_NAME_RE.search(s):
        return True
    if _METADATA_LINE_RE.search(s):
        return True
    if _BULLET_GLYPH_RE.match(s):
        return True
    if _URL_RE.search(s) and len(s.split()) <= 6:
        return True
    if _EMAIL_RE.search(s) and len(s.split()) <= 6:
        return True
    # short ALL-CAPS lines are almost always running headers/footers/titles,
    # not sentences worth mining for questions
    if _SHORT_ALLCAPS_RE.match(s) and len(s.split()) <= 6:
        return True
    # very short lines with no sentence-ending punctuation and no verb-ish
    # content are usually stray headers/labels
    if len(s.split()) <= 3 and not s.endswith((".", "?", "!", ":")):
        return True
    return False


def _find_repeated_lines(pages):
    """Lines that appear on 3+ pages (or on >40% of pages) are treated as
    running headers/footers and stripped everywhere."""
    if len(pages) < 2:
        return set()
    counts = Counter()
    for page in pages:
        seen_this_page = set()
        for line in page.splitlines():
            s = line.strip()
            if s and len(s) < 90:
                seen_this_page.add(s)
        for s in seen_this_page:
            counts[s] += 1
    threshold = max(2, int(len(pages) * 0.4))
    return {s for s, c in counts.items() if c >= threshold}


def clean_pdf_pages(pages) -> str:
    """Given per-page raw text, strip noise and return one clean text blob."""
    repeated = _find_repeated_lines(pages)
    clean_lines = []
    for page in pages:
        for line in page.splitlines():
            if _is_noise_line(line, repeated):
                continue
            clean_lines.append(line.strip())
    text = "\n".join(clean_lines)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def clean_plain_text(text: str) -> str:
    """Fallback cleaner for .txt/.docx input (no per-page structure)."""
    lines = [l for l in (text or "").splitlines()]
    repeated = set()  # no page structure to compare against
    clean_lines = [l.strip() for l in lines if not _is_noise_line(l, repeated)]
    out = "\n".join(clean_lines)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{2,}", "\n\n", out)
    return out.strip()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(
    r"^(chapter\s+\d+|unit\s+\d+|module\s+\d+|\d+(\.\d+)+\s|[A-Z][A-Za-z ]{0,40}:)\s*$"
)

MIN_CHUNK_WORDS = 18


def chunk_text(text: str):
    """Split cleaned text into paragraph-ish chunks, merging short fragments
    into neighbouring chunks so every chunk carries enough context for
    concept/definition extraction."""
    if not text:
        return []

    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(raw_paragraphs) <= 1:
        # no blank-line structure (common with PDF extraction) - fall back to
        # splitting on heading-like lines / long runs of newlines
        raw_paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    merged = []
    buffer = ""
    for para in raw_paragraphs:
        if _HEADING_RE.match(para) and len(para.split()) <= 8:
            continue  # drop bare headings; they carry no answerable content
        buffer = (buffer + " " + para).strip() if buffer else para
        if len(buffer.split()) >= MIN_CHUNK_WORDS:
            merged.append(buffer)
            buffer = ""
    if buffer and len(buffer.split()) >= 6:
        if merged:
            merged[-1] = merged[-1] + " " + buffer
        else:
            merged.append(buffer)

    return merged
