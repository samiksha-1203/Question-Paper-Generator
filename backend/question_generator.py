"""
question_generator.py
----------------------
Turns validated Concept objects (from concept_extraction) into concrete
question/answer candidates of several educational types, each fully
traceable back to its source PDF/module/chunk. Every candidate is run
through validate_question() before it is allowed into the question bank -
this is the last line of defence against meaningless or incomplete
questions reaching the paper.
"""

import re
import uuid
from collections import Counter

from nlp_utils import tokenize_words, cosine_sim, text_vector
from ollama_question_writer import rewrite_candidate_questions

BLOOM_BY_TYPE = {
    "Definition": "Remember",
    "Short Notes": "Understand",
    "Explanation": "Understand",
    "Application": "Apply",
    "Comparison": "Analyze",
    "Analytical": "Evaluate",
    "Problem-Based": "Apply",
}

# baseline marks range per question type - paper_builder scales/selects
# within these bounds to hit the target total marks
MARKS_RANGE = {
    "Definition": (2, 3),
    "Short Notes": (3, 4),
    "Explanation": (4, 6),
    "Application": (4, 6),
    "Comparison": (5, 8),
    "Analytical": (8, 10),
    "Problem-Based": (8, 10),
}

_SENT_END_RE = re.compile(r"[.?!\u0964]\s*$")


TYPE_DIFFICULTY_OFFSET = {
    "Definition": -6,
    "Short Notes": -4,
    "Explanation": 0,
    "Application": 2,
    "Comparison": 4,
    "Analytical": 6,
    "Problem-Based": 6,
}


def classify_difficulty(text: str, type_offset: float = 0.0) -> str:
    words = text.split()
    n_words = len(words) or 1
    avg_word_len = sum(len(w) for w in words) / n_words
    long_words = sum(1 for w in words if len(w) >= 7)
    score = n_words * 0.25 + avg_word_len * 1.5 + long_words * 1.2 + type_offset
    if score < 14:
        return "Easy"
    elif score < 24:
        return "Medium"
    return "Hard"


INTERNAL_FIELDS = ("_definition_sentence", "_context_pool", "_application_sentences",
                   "_adv_disadv_sentences", "_process_sentences")


def strip_internal_fields(q: dict) -> dict:
    for f in INTERNAL_FIELDS:
        q.pop(f, None)
    return q


def sufficient_for_marks(candidate: dict, marks_value: int) -> bool:
    """Guards against assigning a concept with barely any source material to
    a high-marks slot, where a one-line answer would be obviously
    inadequate for what the marks imply."""
    pool = candidate.get("_context_pool") or []
    distinct = len({_clean_sentence_for_answer(s) for s in pool})
    if marks_value >= 8:
        return distinct >= 3
    elif marks_value >= 5:
        return distinct >= 2
    return distinct >= 1


def expand_answer_for_marks(candidate: dict, marks_value: int) -> str:
    """Builds a marks-appropriate model answer from the concept's full
    available source material - a 2-mark answer is a sentence or two; a
    10-mark answer is a structured Introduction / Key Points / Example
    answer, never just a one-liner. Falls back gracefully to whatever
    material actually exists rather than inventing content."""
    pool = candidate.get("_context_pool") or []
    definition = candidate.get("_definition_sentence")
    topic = candidate.get("topic", "")

    # de-duplicate while preserving order
    seen = set()
    ordered_pool = []
    for s in pool:
        c = _clean_sentence_for_answer(s)
        if c and c not in seen:
            seen.add(c)
            ordered_pool.append(c)

    if not ordered_pool:
        return candidate.get("answer", "")

    if marks_value <= 3:
        # 1-2 sentences is genuinely appropriate for 2-3 marks
        base_sent = _clean_sentence_for_answer(definition) if definition else ordered_pool[0]
        rest = [s for s in ordered_pool if s != base_sent]
        parts = [base_sent] + rest[:1]
        return " ".join(parts)

    if marks_value <= 7:
        # a short paragraph: intro + 2-3 supporting sentences
        base_sent = _clean_sentence_for_answer(definition) if definition else ordered_pool[0]
        rest = [s for s in ordered_pool if s != base_sent]
        parts = [base_sent] + rest[:3]
        return " ".join(parts)

    # marks_value >= 8: structured, multi-part answer
    intro = _clean_sentence_for_answer(definition) if definition else ordered_pool[0]
    remaining = [s for s in ordered_pool if s != intro]

    key_points = remaining[:5] if remaining else []
    example_candidates = (
        candidate.get("_application_sentences") or candidate.get("_process_sentences")
        or candidate.get("_adv_disadv_sentences") or []
    )
    example = next(
        (_clean_sentence_for_answer(s) for s in example_candidates
         if _clean_sentence_for_answer(s) not in ({intro} | set(key_points))),
        None,
    )

    lines = [f"Introduction: {intro}", "", "Key Points:"]
    if key_points:
        for i, kp in enumerate(key_points, 1):
            lines.append(f"{i}. {kp}")
    else:
        lines.append(f"1. {intro}")
    if example:
        lines += ["", f"Example / Application: {example}"]
    return "\n".join(lines)


def _clean_sentence_for_answer(sentence: str) -> str:
    s = (sentence or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _summarize_answer(sentences, max_sentences=4):
    parts = [_clean_sentence_for_answer(s) for s in sentences[:max_sentences]]
    parts = [p for p in parts if p]
    return " ".join(parts)


def _title_case(term: str) -> str:
    # keep acronym-like tokens (all caps, length<=5) as-is
    words = term.split()
    out = []
    for w in words:
        if w.isupper() and len(w) <= 5:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def build_candidates(concept, question_types=None):
    """Given one Concept, return a list of raw (unvalidated) question dicts."""
    candidates = []
    term = _title_case(concept.name)
    ctx_sentences = concept.context_sentences(limit=5)
    context_pool = concept.context_sentences(limit=10)  # wider pool for marks-scaled expansion later
    if not ctx_sentences:
        return candidates

    definition_text = concept.definition["sentence"] if concept.definition else None
    module = concept.support[0].get("module") if concept.support else None
    pdf_id = concept.support[0].get("pdf_id") if concept.support else None
    source_chunk_idx = concept.best_chunk_idx

    def base(qtype, question, answer, source_sentences):
        rep_sentence = source_sentences[0] if source_sentences else question
        return {
            "id": str(uuid.uuid4()),
            "type": qtype,
            "topic": term,
            "question": question,
            "answer": answer,
            "module": module,
            "source_pdf": pdf_id,
            "source_chunk_idx": source_chunk_idx,
            "bloom": BLOOM_BY_TYPE[qtype],
            "difficulty": classify_difficulty(rep_sentence, TYPE_DIFFICULTY_OFFSET.get(qtype, 0)),
            "marks_range": MARKS_RANGE[qtype],
            "quality_score": (
                concept.doc_freq
                + min(concept.subject_count, 6) * 0.5
                + (2 if concept.definition else 0)
                + min(len(concept.tags), 3) * 0.5
            ),
            # kept for marks-scaled answer expansion in paper_builder (once
            # the question's final marks value is known); not shown to the
            # user directly.
            "_definition_sentence": definition_text,
            "_context_pool": context_pool,
            "_application_sentences": list(concept.tag_sentences.get("application", [])),
            "_adv_disadv_sentences": list(concept.tag_sentences.get("adv_disadv", [])),
            "_process_sentences": list(concept.tag_sentences.get("process", [])),
        }

    allowed = question_types or set(MARKS_RANGE.keys())

    # --- Definition ---
    if "Definition" in allowed and definition_text:
        answer = _clean_sentence_for_answer(definition_text)
        candidates.append(base(
            "Definition", f"Define {term}.", answer, [definition_text]
        ))

    # --- Explanation ---
    if "Explanation" in allowed and len(ctx_sentences) >= 1:
        answer = _summarize_answer(ctx_sentences, max_sentences=4)
        q = f"Explain the concept of {term} in detail, highlighting its key characteristics."
        candidates.append(base("Explanation", q, answer, ctx_sentences))

    # --- Short Notes ---
    if "Short Notes" in allowed and len(ctx_sentences) >= 1:
        answer = _summarize_answer(ctx_sentences, max_sentences=2)
        candidates.append(base(
            "Short Notes", f"Write a short note on {term}.", answer, ctx_sentences[:2]
        ))

    # --- Comparison ---
    if "Comparison" in allowed and concept.contrast_partner:
        other = _title_case(concept.contrast_partner)
        answer = _summarize_answer(ctx_sentences, max_sentences=3)
        q = f"Differentiate between {term} and {other}, explaining how they differ."
        candidates.append(base("Comparison", q, answer, ctx_sentences))

    # --- Application ---
    if "Application" in allowed and "application" in concept.tags:
        answer = _summarize_answer(ctx_sentences, max_sentences=3)
        q = f"Explain the applications of {term} with suitable examples."
        candidates.append(base("Application", q, answer, ctx_sentences))

    # --- Analytical (advantages/disadvantages) ---
    if "Analytical" in allowed and "adv_disadv" in concept.tags:
        answer = _summarize_answer(ctx_sentences, max_sentences=4)
        q = f"Discuss the advantages and disadvantages (or limitations) of {term}."
        candidates.append(base("Analytical", q, answer, ctx_sentences))

    # --- Problem/Process based ---
    if "Problem-Based" in allowed and "process" in concept.tags:
        answer = _summarize_answer(ctx_sentences, max_sentences=4)
        q = f"Explain the process/steps involved in {term} with the help of an example."
        candidates.append(base("Problem-Based", q, answer, ctx_sentences))

    return candidates


# ---------------------------------------------------------------------------
# Validation - the last checkpoint before a candidate enters the bank
# ---------------------------------------------------------------------------

def validate_question(q: dict) -> bool:
    question = (q.get("question") or "").strip()
    answer = (q.get("answer") or "").strip()
    topic = (q.get("topic") or "").strip()

    if not question or not answer:
        return False
    if len(question.split()) < 4:
        return False
    if question[0].isascii() and question[0].isalpha() and not question[0].isupper():
        return False
    if not _SENT_END_RE.search(question):
        return False
    if len(answer.split()) < 4:
        return False
    # answer shouldn't just be a bare repetition of the topic word
    if answer.lower().strip(".") == (q.get("topic") or "").lower().strip("."):
        return False
    # reject leftover placeholder artifacts
    if re.search(r"\bNone\b|\bnan\b|______", question):
        return False
    normalized_topic = re.sub(r"[^\w]+", " ", topic.casefold()).strip()
    compact_topic = normalized_topic.replace(" ", "")
    if compact_topic in {"types", "advantages", "step", "difference", "inputimage"}:
        return False
    if normalized_topic.startswith("difference between "):
        return False
    question_lower = question.casefold()
    if question_lower.startswith(("according to the source", "what does the source say")):
        return False
    if "highlighting its key characteristics" in question_lower:
        return False
    if "\"" in question and "refers to" in question.lower():
        return False  # guards against illustrative co-reference leakage
    return True


def dedup_candidates(candidates, threshold=0.85):
    kept = []
    kept_vecs = []
    for q in candidates:
        v = text_vector(q["question"] + " " + q["answer"])
        if all(cosine_sim(v, kv) < threshold for kv in kept_vecs):
            kept.append(q)
            kept_vecs.append(v)
        else:
            q["_rejected_reason"] = "duplicate"
    return kept


def generate_question_bank(concepts, question_types=None, progress_callback=None):
    """
    Returns (accepted, stats) where stats tracks how many candidates were
    generated/rejected/duplicated for the quality-metrics dashboard.
    """
    candidate_groups = []
    for c in concepts:
        group = build_candidates(c, question_types=question_types)
        if group:
            candidate_groups.append(group)
    question_writer = rewrite_candidate_questions(
        candidate_groups, validate_question, progress_callback=progress_callback
    )
    raw = [candidate for group in candidate_groups for candidate in group]

    total_generated = len(raw)
    validated = [q for q in raw if validate_question(q)]
    rejected_invalid = total_generated - len(validated)

    deduped = dedup_candidates(validated)
    duplicates_removed = len(validated) - len(deduped)

    stats = {
        "total_candidates": total_generated,
        "accepted": len(deduped),
        "rejected_invalid": rejected_invalid,
        "duplicates_removed": duplicates_removed,
        "question_writer": question_writer,
        "avg_quality_score": (
            round(sum(q["quality_score"] for q in deduped) / len(deduped), 2)
            if deduped else 0
        ),
    }
    return deduped, stats
