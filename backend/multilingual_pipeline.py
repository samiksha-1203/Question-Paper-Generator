"""
multilingual_pipeline.py
-------------------------
The main pipeline (text_extraction -> concept_extraction -> question_generator)
depends on NLTK's English POS tagger and English-specific patterns, so it
cannot understand Hindi/Marathi (Devanagari-script) text. This module is a
deliberately simpler, script-agnostic fallback used ONLY when a PDF's
content is majority-Devanagari, so that Hindi/Marathi source material is at
least usable instead of silently producing nothing.

Trade-off, stated plainly: without a POS tagger for these languages, this
fallback cannot do the fragment/illustrative-sentence filtering that the
English pipeline uses to avoid "Riya"-style nonsense questions. Instead it
leans on a stricter, purely-frequency-based signal (a candidate phrase must
recur across several distinct sentences to qualify at all, which one-off
names/examples essentially never do) and a smaller set of question types
(Definition / Explanation / Short Notes) built from simple, well-tested
Hindi/Marathi sentence patterns. It is lower-fidelity than the English
pipeline by design, not a full translation of it.
"""

import re
import uuid
from collections import Counter

DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")

# A small, high-precision stopword list per language - conservative on
# purpose (missing a stopword just slightly hurts phrase quality; wrongly
# blocking a real content word is worse).
STOPWORDS = {
    "hindi": {
        "और", "या", "है", "हैं", "था", "थे", "थी", "के", "का", "की", "को",
        "में", "से", "पर", "एक", "यह", "वह", "इस", "उस", "जो", "भी", "ही",
        "तो", "लिए", "साथ", "गया", "किया", "करता", "करती", "करते", "होता",
        "होती", "होते", "जाता", "आदि", "तथा", "कि", "यदि", "कोई", "सभी",
        "कुछ", "जैसे", "अपने", "हम", "आप", "वे",
    },
    "marathi": {
        "आणि", "किंवा", "आहे", "आहेत", "होता", "होते", "ची", "चा", "चे",
        "ला", "मध्ये", "पासून", "वर", "एक", "हा", "ही", "हे", "तो", "ती",
        "ते", "जो", "जी", "जे", "सुद्धा", "साठी", "सह", "झाला", "केले",
        "करतो", "करते", "असतो", "असते", "जातो", "इत्यादी", "तसेच", "जर",
        "काही", "सर्व", "जसे", "आपल्या", "आम्ही", "तुम्ही", "ते",
    },
}

DEFINITION_MARKERS = ["कहलाता है", "कहलाती है", "कहा जाता है", "म्हणतात", "असे म्हणतात"]
COPULA = ["है।", "है ", "आहे।", "आहे "]

MIN_PHRASE_LEN = 2
MIN_SUPPORT_SENTENCES = 3  # stricter than the English pipeline, since we
                            # can't filter illustrative/fragment sentences


def is_devanagari_majority(text: str, threshold: float = 0.25) -> bool:
    """True if a meaningful share of the alphabetic characters in `text`
    are Devanagari - i.e. this is predominantly Hindi/Marathi content."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 20:
        return False
    dev = sum(1 for c in letters if DEVANAGARI_RE.match(c))
    return (dev / len(letters)) >= threshold


def _guess_language(text: str) -> str:
    """Best-effort Hindi-vs-Marathi guess from a handful of
    language-distinguishing function words. Defaults to Hindi (the larger
    and more commonly OCR'd of the two) when ambiguous - question templates
    differ slightly per language, but the underlying extraction logic is
    identical either way."""
    marathi_hits = sum(text.count(w) for w in ["आहे", "आहेत", "ची", "चा", "चे"])
    hindi_hits = sum(text.count(w) for w in ["है", "हैं", "का", "की", "के"])
    return "marathi" if marathi_hits > hindi_hits else "hindi"


def _sentence_split(text: str):
    # Devanagari full stop is the danda '।'; also split on '.', '?', '!'
    parts = re.split(r"[।.!?]\s*", text)
    return [p.strip() for p in parts if len(p.strip()) > 0]


def _tokenize(sentence: str):
    return [t for t in re.split(r"\s+", sentence) if t]


def _ngrams(tokens, n):
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _clean(s):
    return re.sub(r"\s+", " ", s).strip()


class _Concept:
    def __init__(self, phrase):
        self.phrase = phrase
        self.sentences = []  # list of sentence strings this phrase appears in
        self.definition = None


def extract_multilingual_concepts(chunks, language, pdf_id=None, module=None):
    stop = STOPWORDS.get(language, set())
    all_sentences = []
    for chunk in chunks:
        all_sentences.extend(_sentence_split(chunk))
    all_sentences = [s for s in all_sentences if len(_tokenize(s)) >= 5]

    phrase_sentence_map = {}
    for sent in all_sentences:
        tokens = [t for t in _tokenize(sent) if t not in stop and len(t) > 1]
        candidates = set()
        for n in (3, 2):
            candidates.update(_ngrams(tokens, n))
        for phrase in candidates:
            phrase_sentence_map.setdefault(phrase, []).append(sent)

    concepts = []
    for phrase, sents in list(phrase_sentence_map.items()):
        distinct_sents = list(dict.fromkeys(sents))  # de-dup, preserve order
        if len(distinct_sents) < MIN_SUPPORT_SENTENCES:
            continue
        if len(phrase.split()) < MIN_PHRASE_LEN:
            continue
        c = _Concept(phrase)
        c.sentences = distinct_sents
        for marker in DEFINITION_MARKERS:
            for s in distinct_sents:
                if marker in s and phrase in s:
                    c.definition = s
                    break
            if c.definition:
                break
        concepts.append(c)

    # prefer concepts backed by more distinct sentences (stronger recurrence
    # signal), and among ties prefer the LONGER phrase (e.g. "नामित इकाई
    # पहचान" over the shorter substring "नामित इकाई") since it's more
    # specific and more likely to be the actual taught term
    concepts.sort(key=lambda c: (len(c.sentences), len(c.phrase.split())), reverse=True)

    deduped = []
    seen_sentence_sets = []
    for c in concepts:
        sset = set(c.sentences)
        if any(len(sset & prev) / max(1, len(sset | prev)) > 0.7 for prev in seen_sentence_sets):
            continue
        seen_sentence_sets.append(sset)
        deduped.append(c)

    candidates = []
    templates = _TEMPLATES.get(language, _TEMPLATES["hindi"])
    for c in deduped:
        answer = _clean(c.definition) if c.definition else _clean(" ".join(c.sentences[:3]))
        if len(answer.split()) < 4:
            continue

        candidates.append({
            "id": str(uuid.uuid4()),
            "type": "Definition" if c.definition else "Explanation",
            "topic": c.phrase,
            "question": templates["definition" if c.definition else "explanation"].format(term=c.phrase),
            "answer": answer,
            "module": module,
            "source_pdf": pdf_id,
            "source_chunk_idx": None,
            "bloom": "Remember" if c.definition else "Understand",
            "difficulty": _classify_difficulty_ml(answer),
            "marks_range": (2, 3) if c.definition else (4, 6),
            "quality_score": len(c.sentences),
            "_definition_sentence": c.definition,
            "_context_pool": c.sentences[:8],
            "_application_sentences": [],
            "_adv_disadv_sentences": [],
            "_process_sentences": [],
        })
        # also offer a short-notes variant when there's enough material
        if len(c.sentences) >= MIN_SUPPORT_SENTENCES:
            short_answer = _clean(" ".join(c.sentences[:2]))
            candidates.append({
                "id": str(uuid.uuid4()),
                "type": "Short Notes",
                "topic": c.phrase,
                "question": templates["short_notes"].format(term=c.phrase),
                "answer": short_answer,
                "module": module,
                "source_pdf": pdf_id,
                "source_chunk_idx": None,
                "bloom": "Understand",
                "difficulty": _classify_difficulty_ml(short_answer),
                "marks_range": (3, 4),
                "quality_score": len(c.sentences) - 1,
                "_definition_sentence": c.definition,
                "_context_pool": c.sentences[:8],
                "_application_sentences": [],
                "_adv_disadv_sentences": [],
                "_process_sentences": [],
            })

    return candidates


_TEMPLATES = {
    "hindi": {
        "definition": "{term} को परिभाषित करें।",
        "explanation": "{term} के बारे में विस्तार से समझाइए।",
        "short_notes": "{term} पर संक्षिप्त टिप्पणी लिखें।",
    },
    "marathi": {
        "definition": "{term} ची व्याख्या करा.",
        "explanation": "{term} बद्दल सविस्तर स्पष्ट करा.",
        "short_notes": "{term} वर थोडक्यात टीप लिहा.",
    },
}


def _classify_difficulty_ml(text: str) -> str:
    n_words = len(text.split()) or 1
    if n_words < 10:
        return "Easy"
    elif n_words < 20:
        return "Medium"
    return "Hard"


def build_candidates_for_pdf_multilingual(pdf_id, chunks, module, language=None):
    lang = language or _guess_language(" ".join(chunks[:3]))
    raw = extract_multilingual_concepts(chunks, lang, pdf_id=pdf_id, module=module)

    # validation: same spirit as the English validator, but without the
    # English-specific regexes
    validated = []
    for q in raw:
        if not q["question"].strip() or not q["answer"].strip():
            continue
        if len(q["answer"].split()) < 4:
            continue
        validated.append(q)

    stats = {
        "total_candidates": len(raw),
        "accepted": len(validated),
        "rejected_invalid": len(raw) - len(validated),
        "duplicates_removed": 0,
        "avg_quality_score": (
            round(sum(q["quality_score"] for q in validated) / len(validated), 2) if validated else 0
        ),
    }
    return validated, stats, len(chunks), len({q["topic"] for q in validated})
