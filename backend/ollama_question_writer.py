"""Optional local Ollama question wording, with no external Python dependencies."""

import json
import os
import re
import uuid
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from nlp_utils import sent_tokenize_safe

DEFAULT_MODEL = "qwen3:1.7b"
BATCH_SIZE = 4
SOURCE_BATCH_SIZE = 8
SOURCE_UNIT_WORDS = 180
MAX_SOURCE_UNITS_PER_PDF = 32

SOURCE_QUESTION_TYPES = {
    "Definition", "Short Notes", "Explanation", "Application",
    "Comparison", "Analytical", "Problem-Based",
}
SOURCE_MARKS_RANGE = {
    "Definition": (2, 3),
    "Short Notes": (3, 4),
    "Explanation": (4, 6),
    "Application": (4, 6),
    "Comparison": (5, 8),
    "Analytical": (8, 10),
    "Problem-Based": (8, 10),
}
SOURCE_BLOOM = {
    "Definition": "Remember",
    "Short Notes": "Understand",
    "Explanation": "Understand",
    "Application": "Apply",
    "Comparison": "Analyze",
    "Analytical": "Evaluate",
    "Problem-Based": "Apply",
}
GENERIC_TOPICS = {"types", "advantages", "step", "difference", "input image"}
TOPIC_GENERIC_TERMS = {
    "neural", "network", "networks", "system", "systems", "model", "models",
    "computer", "science", "process", "processing", "image", "images", "data",
    "method", "methods", "technique", "techniques", "approach", "approaches",
    "layer", "layers", "type", "types", "operation", "operations",
}
QUESTION_STOP_WORDS = {
    "what", "when", "where", "why", "how", "which", "who", "whom", "whose",
    "is", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "can", "could", "should", "would", "will", "may", "might", "must", "have",
    "has", "had", "the", "a", "an", "of", "in", "on", "at", "to", "for", "and",
    "or", "but", "by", "with", "from", "as", "than", "into", "through", "about",
    "across", "between", "their", "its", "this", "these", "those", "it", "they",
    "we", "you", "your", "according", "source", "say", "says", "describe", "explain",
    "discuss", "state", "write", "purpose", "role", "contribute", "contributes",
    "improve", "improves", "improving", "help", "helps", "benefit", "benefits",
    "effective", "effectively", "efficiency", "significance", "important", "main",
    "primary", "function", "context", "text", "passage",
    "application", "applications", "differ", "differs", "difference", "differences",
    "compare", "compares", "comparison", "choice", "choices", "affect", "affects",
    "effect", "effects", "two", "both", "discussed", "types",
    "process", "processing", "effectiveness", "performance", "involve", "involves",
    "impact", "impacts", "neural", "network", "networks", "cnn", "play", "plays",
}


class OllamaQuestionGenerationError(RuntimeError):
    pass


def _decode_json_response(raw_content):
    """Decode model output even when Ollama wraps JSON in markdown fences or stray text."""
    if raw_content is None:
        raise ValueError("Ollama returned an empty response")

    content = str(raw_content).strip()
    if not content:
        raise ValueError("Ollama returned an empty response")

    cleaned = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.IGNORECASE)

    candidates = [cleaned]
    for marker in ("{", "["):
        if marker in cleaned:
            start = cleaned.find(marker)
            end = max(cleaned.rfind("}"), cleaned.rfind("]"))
            if start >= 0 and end > start:
                candidates.append(cleaned[start:end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    # Last resort: recover the smallest valid JSON object/array segment from a noisy payload.
    for start_index, start_char in enumerate(cleaned):
        if start_char not in "[{":
            continue
        for end_index in range(len(cleaned), start_index, -1):
            chunk = cleaned[start_index:end_index]
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Invalid JSON returned by Ollama: {content[:250]}")


def _grounding_terms(text):
    terms = set()
    for word in re.findall(r"[A-Za-z0-9]+", text.casefold()):
        if word in QUESTION_STOP_WORDS or len(word) < 3:
            continue
        normalized = word
        if len(normalized) > 5 and normalized.endswith("ies"):
            normalized = normalized[:-3] + "y"
        elif len(normalized) > 5 and normalized.endswith("ing"):
            normalized = normalized[:-3]
            if normalized.endswith("at"):
                terms.add(normalized + "e")
        elif len(normalized) > 4 and normalized.endswith("ed"):
            normalized = normalized[:-2]
            if normalized.endswith("at"):
                terms.add(normalized + "e")
        elif len(normalized) > 5 and normalized.endswith(("ches", "shes", "xes", "zes")):
            normalized = normalized[:-2]
        elif len(normalized) > 4 and normalized.endswith("s") and not normalized.endswith(("ss", "us", "is")):
            normalized = normalized[:-1]
        terms.add(normalized)
    normalized_text = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    if re.search(r"\bconvolutional neural networks?\b", normalized_text):
        terms.add("cnn")
    if re.search(r"\bcnns?\b", normalized_text):
        terms.add("cnn")
    if re.search(r"\brecurrent neural networks?\b", normalized_text):
        terms.add("rnn")
    if re.search(r"\blong short term memory\b", normalized_text):
        terms.add("lstm")
    return terms


def _is_grounded_in_citations(topic, question, answer, cited_sentences):
    topic_terms = _grounding_terms(topic) - TOPIC_GENERIC_TERMS
    if not topic_terms or not cited_sentences:
        return False
    cited_terms = set().union(*(_grounding_terms(sentence) for sentence in cited_sentences))
    question_terms = _grounding_terms(question) - topic_terms
    answer_terms = _grounding_terms(answer)
    return (
        bool(topic_terms.intersection(cited_terms))
        and question_terms.issubset(cited_terms)
        and answer_terms.issubset(cited_terms)
    )


def _source_units(chunks):
    units = []
    current = []
    word_count = 0
    unit_idx = 0

    def flush():
        nonlocal current, word_count, unit_idx
        if current:
            units.append({
                "id": f"u{unit_idx}",
                "chunk_idx": current[0]["chunk_idx"],
                "sentences": current,
            })
            unit_idx += 1
            current = []
            word_count = 0

    for chunk_idx, chunk in enumerate(chunks):
        sentences = sent_tokenize_safe(chunk)
        if len(sentences) <= 1:
            sentences = re.split(r"(?<=[.!?।])\s+", chunk.strip())
        for sentence_idx, sentence in enumerate(sentences):
            sentence = " ".join(sentence.split())
            if not sentence:
                continue
            sentence_words = len(sentence.split())
            if current and word_count + sentence_words > SOURCE_UNIT_WORDS:
                flush()
            current.append({
                "id": f"c{chunk_idx}s{sentence_idx}",
                "chunk_idx": chunk_idx,
                "text": sentence,
            })
            word_count += sentence_words
    flush()
    return units


def _sample_source_units(units, limit):
    """Evenly sample long documents to bound model calls without page bias."""
    if limit <= 0 or len(units) <= limit:
        return units
    if limit == 1:
        return [units[len(units) // 2]]
    last_index = len(units) - 1
    indices = [round(index * last_index / (limit - 1)) for index in range(limit)]
    return [units[index] for index in indices]


def _generation_source_units(chunks, use_all_material):
    units = _source_units(chunks)
    return units if use_all_material else _sample_source_units(units, MAX_SOURCE_UNITS_PER_PDF)


def _request_source_questions(units, model, education_level, source_language):
    schema = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                        "type": {"type": "string", "enum": sorted(SOURCE_QUESTION_TYPES)},
                        "difficulty": {"type": "string", "enum": ["Easy", "Medium", "Hard"]},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "topic", "question", "answer", "type", "difficulty", "evidence_ids",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["questions"],
        "additionalProperties": False,
    }
    prompt = (
        "You are an experienced exam setter. Read the supplied course sentences and "
        "create at most one strong exam-question candidate per source unit. "
        "First identify concepts that are genuinely taught by the passage; do not turn "
        "headings, OCR fragments, generic labels such as 'types', 'advantages', or "
        "'step', or isolated example names into topics. Use a specific topic name, not "
        "a fragment such as 'difference between ...'. Avoid repeating a topic or asking "
        "the same thing with different wording. Ask precise questions that test an idea "
        "explained in the source, not generic 'highlight its key characteristics' prompts. "
        "Do not write phrases like 'according to the source' or 'what does the source say'. "
        "Match question type to evidence: a one-sentence fact may only support a Definition; "
        "Short Notes, Explanation, or Application must cite at least two distinct sentences; "
        "Comparison, Analytical, or Problem-Based questions must cite at least three distinct "
        "sentences. If those sentences are not present, choose a simpler supported type or "
        "omit the question. Return at most one candidate per source unit. "
        "Write a concise answer appropriate to the question type. "
        "Use only facts explicitly stated in the cited sentences; do not add likely uses, "
        "benefits, examples, implications, or textbook facts unless the citations state them. "
        "Every factual phrase in both the question and answer must be supported by the "
        "cited source sentences; cite all sentences needed to support the question context, "
        "not just its main topic. Cite only the two to four necessary sentence IDs; never "
        "return every ID in a source unit. Do not use outside facts. Return exact IDs. "
        "If the source unit does not support a meaningful question, return no question "
        "for it. Adapt the depth to education level: " + str(education_level) + ". "
        "Write topic, question, and answer in this language: " + source_language + ".\n\n"
        + json.dumps({"source_units": units}, ensure_ascii=False)
    )
    base_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": schema,
        "think": False,
        "messages": [
            {"role": "system", "content": "Write source-grounded, specific examination questions."},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.2, "top_p": 0.85, "num_predict": 1536, "num_ctx": 4096},
    }).encode("utf-8")
    request = Request(
        f"{base_url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama returned HTTP {error.code}: {detail}") from error
    content = payload.get("message", {}).get("content", "")
    decoded = _decode_json_response(content)
    return decoded.get("questions", []) if isinstance(decoded, dict) else []


def generate_source_questions(chunks, pdf_id, module, validator, progress_callback=None,
                              education_level="General / Mixed", source_language="English",
                              use_all_material=True):
    """Ask the local model to identify taught concepts and draft cited questions."""
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip()
    if not model or model.casefold() in {"off", "none", "template"}:
        raise OllamaQuestionGenerationError(
            "Model-based question generation is disabled. Set OLLAMA_MODEL to an installed model."
        )

    units = _generation_source_units(chunks, use_all_material)
    batches = [
        units[index:index + SOURCE_BATCH_SIZE]
        for index in range(0, len(units), SOURCE_BATCH_SIZE)
    ]
    if not batches:
        return [], {
            "total_candidates": 0, "accepted": 0, "rejected_invalid": 0,
            "duplicates_removed": 0, "question_writer": f"{model} via local Ollama",
            "avg_quality_score": 0,
        }

    candidates = []
    seen_topics = set()
    per_unit_counts = Counter()
    unit_by_sentence = {
        sentence["id"]: unit["id"]
        for unit in units
        for sentence in unit["sentences"]
    }
    total_generated = 0
    completed_batches = 0
    for batch_idx, batch in enumerate(batches, 1):
        if progress_callback:
            progress_callback(batch_idx - 1, len(batches))
        sentence_lookup = {
            sentence["id"]: (sentence["chunk_idx"], sentence["text"])
            for unit in batch
            for sentence in unit["sentences"]
        }
        try:
            generated = _request_source_questions(
                batch, model, education_level, source_language
            )
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
            if not completed_batches:
                raise OllamaQuestionGenerationError(
                    f"Could not generate questions with Ollama ({model}): {error}. "
                    "Start Ollama and confirm the model is installed."
                ) from error
            break

        for item in generated:
            total_generated += 1
            if not isinstance(item, dict):
                continue
            topic = " ".join(str(item.get("topic") or "").split())
            topic_key = re.sub(r"[^\w]+", " ", topic.casefold()).strip()
            compact_topic = topic_key.replace(" ", "")
            question = " ".join(str(item.get("question") or "").split())
            answer = " ".join(str(item.get("answer") or "").split())
            question = re.sub(
                r"^according to (?:the )?source(?: text)?[,]?\s*|\s+according to (?:the )?source(?: text)?",
                "",
                question,
                flags=re.IGNORECASE,
            ).strip()
            question = re.sub(
                r"^what does (?:the )?source say about (?:the )?role of (.+?)[?.]?$",
                r"What role does \1 play?",
                question,
                flags=re.IGNORECASE,
            )
            question = re.sub(
                r"^what does (?:the )?source say about (?:the )?purpose of (.+?)[?.]?$",
                r"What is the purpose of \1?",
                question,
                flags=re.IGNORECASE,
            )
            question = re.sub(
                r"^what does (?:the )?source say about (.+?)[?.]?$",
                r"Explain \1.",
                question,
                flags=re.IGNORECASE,
            )
            question = re.sub(
                r"^which sentence from (?:the )?source unit supports the idea that (.+?)[?.]?$",
                r"Explain \1.",
                question,
                flags=re.IGNORECASE,
            )
            question = re.sub(
                r"^which sentence from (?:the )?source unit (?:explains|describes) (.+?)[?.]?$",
                r"Explain \1.",
                question,
                flags=re.IGNORECASE,
            )
            question = re.sub(
                r"^which sentence (?:explicitly )?states the purpose of (.+?)[?.]?$",
                r"What is the purpose of \1?",
                question,
                flags=re.IGNORECASE,
            )
            question = re.sub(
                r"^which sentence describes the role of (.+?)[?.]?$",
                r"What role does \1 play?",
                question,
                flags=re.IGNORECASE,
            )
            question = re.sub(
                r"^which sentence explains the function of (.+?)[?.]?$",
                r"What function does \1 serve?",
                question,
                flags=re.IGNORECASE,
            )
            if question and question[0].isascii() and question[0].isalpha():
                question = question[0].upper() + question[1:]
            qtype = item.get("type")
            evidence_ids = item.get("evidence_ids")
            if (
                not topic_key or compact_topic in {value.replace(" ", "") for value in GENERIC_TOPICS}
                or topic_key in seen_topics
                or qtype not in SOURCE_QUESTION_TYPES
                or item.get("difficulty") not in {"Easy", "Medium", "Hard"}
                or not isinstance(evidence_ids, list) or not evidence_ids
            ):
                continue
            cited_sentences = []
            cited_chunks = []
            for evidence_id in evidence_ids:
                citation = sentence_lookup.get(evidence_id)
                if citation:
                    cited_chunks.append(citation[0])
                    if citation[1] not in cited_sentences:
                        cited_sentences.append(citation[1])
            if any(evidence_id not in sentence_lookup for evidence_id in evidence_ids):
                continue
            primary_unit = unit_by_sentence.get(evidence_ids[0])
            if not primary_unit or per_unit_counts[primary_unit] >= 1:
                continue
            if source_language == "English":
                source_unit = next((unit for unit in batch if unit["id"] == primary_unit), None)
                if not source_unit:
                    continue
                if not _is_grounded_in_citations(topic, question, answer, cited_sentences):
                    continue
            if not cited_sentences:
                continue

            question_lower = question.casefold()
            if "highlighting its key characteristics" in question_lower:
                continue
            candidate = {
                "id": str(__import__("uuid").uuid4()),
                "type": qtype,
                "topic": topic,
                "question": question,
                "answer": answer,
                "module": module,
                "source_pdf": pdf_id,
                "source_chunk_idx": cited_chunks[0],
                "source_evidence": cited_sentences,
                "bloom": SOURCE_BLOOM[qtype],
                "difficulty": item["difficulty"],
                "marks_range": SOURCE_MARKS_RANGE[qtype],
                "quality_score": len(cited_sentences),
                "_definition_sentence": None,
                "_context_pool": cited_sentences,
                "_application_sentences": cited_sentences if qtype == "Application" else [],
                "_adv_disadv_sentences": cited_sentences if qtype == "Analytical" else [],
                "_process_sentences": cited_sentences if qtype == "Problem-Based" else [],
            }
            if validator(candidate):
                seen_topics.add(topic_key)
                per_unit_counts[primary_unit] += 1
                candidates.append(candidate)
        completed_batches += 1
        if progress_callback:
            progress_callback(completed_batches, len(batches))

    stats = {
        "total_candidates": total_generated,
        "accepted": len(candidates),
        "rejected_invalid": max(0, total_generated - len(candidates)),
        "duplicates_removed": 0,
        "question_writer": (
            f"{model} via local Ollama" if completed_batches == len(batches)
            else f"{model} via local Ollama (some source batches skipped)"
        ),
        "avg_quality_score": (
            round(sum(q["quality_score"] for q in candidates) / len(candidates), 2)
            if candidates else 0
        ),
    }
    return candidates, stats


def _request_rewrites(concept_groups, model):
    concepts = []
    for group in concept_groups:
        if not group:
            continue
        concepts.append({
            "topic": group[0].get("topic", ""),
            "candidates": [
                {
                    "id": candidate["id"],
                    "intent": candidate["type"],
                    "current_question": candidate["question"],
                    "source_answer": candidate["answer"],
                    "source_evidence": (candidate.get("_context_pool") or [])[:6],
                }
                for candidate in group
            ],
        })
    prompt = (
        "Rewrite each exam question so it sounds natural and distinct from the other "
        "questions for the same topic. Keep its educational intent. The question must "
        "be answerable completely from its supplied source answer and evidence. Do not "
        "invent facts, ask for unsupported calculations or comparisons, or change the "
        "topic. Return only the requested JSON.\n\n"
        + json.dumps({"concepts": concepts}, ensure_ascii=True)
    )
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "question": {"type": "string"},
                    },
                    "required": ["id", "question"],
                },
            },
        },
        "required": ["items"],
    }
    base_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    body = json.dumps({
        "model": model,
        "stream": False,
        "format": schema,
        "think": False,
        "messages": [
            {"role": "system", "content": "You write clear, source-grounded exam questions."},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.7, "top_p": 0.9},
    }).encode("utf-8")
    request = Request(
        f"{base_url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload.get("message", {}).get("content", "")
    decoded = _decode_json_response(content)
    return decoded.get("items", []) if isinstance(decoded, dict) else []


def rewrite_candidate_questions(candidate_groups, validator, progress_callback=None):
    """Rewrite only candidate prompts; source-derived answers stay unchanged."""
    if not candidate_groups:
        return "No candidates to rewrite"
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip()
    if not model or model.casefold() in {"off", "none", "template"}:
        return "Template fallback (Ollama disabled)"

    batches = [
        candidate_groups[index:index + BATCH_SIZE]
        for index in range(0, len(candidate_groups), BATCH_SIZE)
    ]
    if progress_callback:
        progress_callback(0, len(batches))
    completed_batches = 0
    for batch in batches:
        try:
            rewrites = _request_rewrites(batch, model)
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            break

        by_id = {
            candidate["id"]: candidate
            for group in batch
            for candidate in group
        }
        for item in rewrites:
            if not isinstance(item, dict):
                continue
            candidate = by_id.get(item.get("id"))
            question = item.get("question")
            if not candidate or not isinstance(question, str):
                continue
            question = " ".join(question.split())
            proposed = dict(candidate, question=question)
            topic = candidate.get("topic", "").casefold()
            if topic and topic in question.casefold() and validator(proposed):
                candidate["question"] = question
        completed_batches += 1
        if progress_callback:
            progress_callback(completed_batches, len(batches))

    if completed_batches == len(batches):
        return f"{model} via local Ollama"
    if completed_batches:
        return f"{model} via local Ollama (remaining prompts use templates)"
    return "Template fallback (Ollama unavailable or model not downloaded)"