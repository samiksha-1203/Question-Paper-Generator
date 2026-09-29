"""
ocr_extraction.py
------------------
Fallback text extraction for scanned/image-only PDFs, where pypdf's normal
text layer extraction returns little or nothing because the "text" is
actually a picture of text. Renders each page to an image and runs
Tesseract OCR over it.

Also supports OCR in Hindi and Marathi (in addition to English) via
Tesseract's language packs, for scanned Indian-language course material.
Multiple languages can be combined (Tesseract will try all of them per
page), which is useful for pages that mix English technical terms with
Hindi/Marathi explanation text.

Requires the Tesseract OCR binary and poppler (for pdf2image) to be
installed on the system - see README for install commands. If they aren't
installed, functions here raise a clear, catchable error rather than
crashing the whole upload; the caller falls back to "no text could be
extracted" instead of a stack trace.
"""

import os
from concurrent.futures import ThreadPoolExecutor

LANGUAGE_CODES = {
    "english": "eng",
    "hindi": "hin",
    "marathi": "mar",
}

# Tesseract-installed-language cache (populated lazily)
_available_langs_cache = None


def _poppler_path():
    """Find a standard Windows Poppler install when it is not on PATH."""
    if os.name != "nt":
        return None
    candidates = []
    for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
        if root:
            candidates.extend([
                os.path.join(root, "poppler", "Library", "bin"),
                os.path.join(root, "poppler-26.09.0", "Library", "bin"),
            ])
    for directory in candidates:
        if os.path.isfile(os.path.join(directory, "pdftoppm.exe")):
            return directory
    return None


class OCRUnavailableError(RuntimeError):
    """Raised when Tesseract/poppler aren't installed on this machine."""
    pass


def _check_dependencies():
    try:
        import pytesseract
    except ImportError as e:
        raise OCRUnavailableError(
            "pytesseract is not installed. Run: pip install pytesseract pdf2image"
        ) from e
    try:
        pytesseract.get_tesseract_version()
    except Exception as e:
        raise OCRUnavailableError(
            "The Tesseract OCR engine isn't installed or isn't on PATH. "
            "See README for install commands for your OS."
        ) from e


def available_languages():
    """Which OCR languages are actually installed on this machine, out of
    the ones this app offers (english/hindi/marathi)."""
    global _available_langs_cache
    if _available_langs_cache is not None:
        return _available_langs_cache
    try:
        import pytesseract
        installed = set(pytesseract.get_languages(config=""))
    except Exception:
        installed = set()
    _available_langs_cache = {
        name: code for name, code in LANGUAGE_CODES.items() if code in installed
    }
    return _available_langs_cache


def resolve_lang_string(requested_languages):
    """requested_languages: list of display names, e.g. ["english", "hindi"].
    Returns a Tesseract-style '+'-joined language string, falling back to
    'eng' if nothing requested/available matches, and silently drops any
    requested language whose data pack isn't actually installed."""
    available = available_languages()
    codes = [available[l] for l in (requested_languages or []) if l in available]
    if not codes:
        codes = ["eng"] if "eng" in available.values() or not available else [list(available.values())[0]]
    # de-dup while preserving order
    seen = []
    for c in codes:
        if c not in seen:
            seen.append(c)
    return "+".join(seen) or "eng"


def is_scanned_pdf(pages_text, min_chars_per_page=25):
    """Heuristic: if most pages produced almost no extractable text via the
    normal PDF text layer, this is very likely a scanned/image-only PDF."""
    if not pages_text:
        return False
    empty_pages = sum(1 for p in pages_text if len(p.strip()) < min_chars_per_page)
    return empty_pages / len(pages_text) >= 0.6


def ocr_pdf_bytes(file_bytes: bytes, languages=None, dpi=200, max_pages=60):
    """Renders each page of the PDF to an image and OCRs it.
    languages: list of display names (e.g. ["english", "hindi"]).
    Returns a list of per-page OCR'd text strings.
    Raises OCRUnavailableError if Tesseract/poppler aren't installed."""
    _check_dependencies()
    import pytesseract
    try:
        from pdf2image import convert_from_bytes
    except ImportError as e:
        raise OCRUnavailableError(
            "pdf2image is not installed. Run: pip install pdf2image"
        ) from e

    lang_string = resolve_lang_string(languages)

    try:
        poppler_path = _poppler_path()
        images = convert_from_bytes(
            file_bytes,
            dpi=dpi,
            first_page=1,
            last_page=max_pages,
            thread_count=4,
            **({"poppler_path": poppler_path} if poppler_path else {}),
        )
    except Exception as e:
        raise OCRUnavailableError(
            f"Could not rasterize the PDF for OCR (is poppler installed?): {e}"
        ) from e

    def recognize(img):
        try:
            return pytesseract.image_to_string(img, lang=lang_string)
        except Exception:
            return ""

    images = images[:max_pages]
    if not images:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(images))) as executor:
        return list(executor.map(recognize, images))
