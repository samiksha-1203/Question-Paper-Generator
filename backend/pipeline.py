"""
pipeline.py
-----------
Glue module: runs the full text -> chunks -> concepts -> validated
question-candidates pipeline for a single PDF, and aggregates results
across all uploaded PDFs into one candidate bank + generation stats.
"""

import os

from text_extraction import chunk_text
from concept_extraction import extract_concepts
from question_generator import generate_question_bank, validate_question
from ollama_question_writer import DEFAULT_MODEL, generate_source_questions
from multilingual_pipeline import is_devanagari_majority, build_candidates_for_pdf_multilingual


def build_candidates_for_pdf(pdf_id, clean_text, module, progress_callback=None,
                             education_level="General / Mixed", use_all_material=True):
    if progress_callback:
        progress_callback(0.05, "Extracting text and preparing sections")
    chunks = chunk_text(clean_text)

    is_multilingual = is_devanagari_majority(clean_text)
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip().casefold()
    if model in {"off", "none", "template"} and is_multilingual:
        # Hindi/Marathi source material - the English POS-tagger-based
        # pipeline can't parse Devanagari script, so fall back to the
        # simpler, script-agnostic extractor (see multilingual_pipeline.py).
        if progress_callback:
            progress_callback(0.45, "Finding recurring Hindi/Marathi concepts")
        result = build_candidates_for_pdf_multilingual(pdf_id, chunks, module)
        if progress_callback:
            progress_callback(0.95, "Validating generated questions")
        return result

    if model in {"off", "none", "template"}:
        if progress_callback:
            progress_callback(0.3, "Analyzing concepts and source evidence")
        concepts = extract_concepts(chunks, pdf_id=pdf_id, module=module)
        candidates, stats = generate_question_bank(concepts)
        return candidates, stats, len(chunks), len(concepts)

    if progress_callback:
        progress_callback(0.3, "Reading source passages for question generation")

    def report_model_progress(completed, total):
        if not progress_callback:
            return
        fraction = 0.5 if not total else 0.5 + 0.42 * completed / total
        message = (
            "Preparing source-grounded questions"
            if not total
            else f"Generating source-grounded question batch {completed} of {total}"
        )
        progress_callback(fraction, message)

    language = "Hindi/Marathi (use the source's own language and script)" if is_multilingual else "English"
    try:
        candidates, stats = generate_source_questions(
            chunks,
            pdf_id,
            module,
            validate_question,
            progress_callback=report_model_progress,
            education_level=education_level,
            source_language=language,
            use_all_material=use_all_material,
        )
    except Exception:
        candidates, stats = [], {
            "total_candidates": 0,
            "accepted": 0,
            "rejected_invalid": 0,
            "duplicates_removed": 0,
            "question_writer": "Template fallback",
        }

    if not candidates:
        if progress_callback:
            progress_callback(0.7, "The local model returned no valid source-grounded questions; falling back to the template pipeline")
        concepts = extract_concepts(chunks, pdf_id=pdf_id, module=module)
        candidates, stats = generate_question_bank(concepts)
        if progress_callback:
            progress_callback(0.95, "Validating fallback questions and removing duplicates")
        return candidates, stats, len(chunks), len(concepts)

    if progress_callback:
        progress_callback(0.95, "Validating source evidence and removing duplicates")
    return candidates, stats, len(chunks), len({q["topic"] for q in candidates})


def build_candidate_bank(pdfs: dict, progress_callback=None,
                         education_level="General / Mixed", use_all_material=True):
    """
    pdfs: {pdf_id: {"clean_text": str, "module": str, ...}}
    Returns (all_candidates, aggregate_stats, per_pdf_meta)
    """
    all_candidates = []
    agg_stats = {"total_candidates": 0, "accepted": 0, "rejected_invalid": 0, "duplicates_removed": 0}
    quality_scores = []
    question_writers = set()
    per_pdf_meta = {}

    pdf_items = list(pdfs.items())
    total_pdfs = len(pdf_items)
    for index, (pdf_id, meta) in enumerate(pdf_items):
        module = meta.get("module") or "Unassigned"
        filename = meta.get("filename") or f"File {index + 1}"
        file_start = 5 + 70 * index / max(total_pdfs, 1)
        file_span = 70 / max(total_pdfs, 1)

        def report_file_progress(fraction, message):
            if progress_callback:
                progress_callback(
                    file_start + file_span * fraction,
                    f"{filename}: {message}",
                )

        report_file_progress(0, f"Starting file {index + 1} of {total_pdfs}")
        candidates, stats, n_chunks, n_concepts = build_candidates_for_pdf(
            pdf_id, meta.get("clean_text", ""), module,
            progress_callback=report_file_progress,
            education_level=education_level,
            use_all_material=use_all_material,
        )
        report_file_progress(1, f"File {index + 1} of {total_pdfs} complete")
        all_candidates.extend(candidates)
        question_writers.add(stats.get("question_writer", "Template writer (multilingual)"))
        for k in ("total_candidates", "accepted", "rejected_invalid", "duplicates_removed"):
            agg_stats[k] += stats[k]
        quality_scores.extend(q["quality_score"] for q in candidates)
        per_pdf_meta[pdf_id] = {"n_chunks": n_chunks, "n_concepts": n_concepts, **stats}

    agg_stats["avg_quality_score"] = round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else 0
    agg_stats["question_writer"] = "; ".join(sorted(question_writers))
    return all_candidates, agg_stats, per_pdf_meta
