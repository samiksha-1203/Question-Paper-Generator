"""
app.py
------
Flask backend for the NLP Question Paper Generator (v2).

The whole app operates on a single in-memory "project" (this is a local,
single-user tool - there is no login/DB). The project holds:
  - uploaded PDFs (cleaned text + per-PDF module/weightage assignment)
  - module list + weightage, difficulty-mix weightage, paper config
  - the generated candidate question bank (built once per /api/generate)
  - the current editable paper
  - a PaperBuilder instance, kept around so individual-question
    regeneration can pull from the same unused-candidate pool.

Restarting the server clears the project (see README for details).
"""

import os
import io
import math
import threading
import traceback
import uuid

from flask import Flask, request, jsonify, send_file, render_template

from text_extraction import (
    extract_pages_from_pdf, extract_text_from_docx, extract_pages_from_pptx,
    clean_pdf_pages, clean_plain_text,
)
from ocr_extraction import ocr_pdf_bytes, is_scanned_pdf, OCRUnavailableError, available_languages
from pipeline import build_candidate_bank
from paper_builder import PaperBuilder, compute_dashboard, compute_total_marks, _group_label
from pdf_export import build_paper_pdf, build_answer_key_pdf
from docx_export import build_paper_docx, build_answer_key_docx

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 60 MB upload cap (multiple PDFs)


def _new_project():
    return {
        "pdfs": {},           # pdf_id -> {filename, module, weightage, clean_text, word_count, page_count}
        "modules": [{"name": "Module 1", "weightage": 100}],
        "difficulty_mix": {"Easy": 30, "Medium": 40, "Hard": 30},
        "config": {
            "title": "Question Paper", "subject": "General", "duration": "120 minutes",
            "education_level": "General / Mixed",
            "use_all_material": True,
            "marks_scheme": [
                {"marks": 2, "count": 6},
                {"marks": 5, "count": 6},
                {"marks": 10, "count": 4},
            ],
            "instructions": [
                "All questions are compulsory unless stated otherwise.",
                "Figures to the right indicate full marks.",
            ],
        },
        "candidates": [],
        "gen_stats": {},
        "per_pdf_meta": {},
        "paper": None,
        "builder": None,
    }


PROJECT = _new_project()
GENERATION_STATE = {"job_id": None, "status": "idle"}
GENERATION_LOCK = threading.Lock()


def _clear_generated_paper():
    PROJECT["paper"] = None
    PROJECT["builder"] = None
    PROJECT["candidates"] = []
    PROJECT["gen_stats"] = {}
    PROJECT["per_pdf_meta"] = {}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# PDF / material management
# ---------------------------------------------------------------------------
def _pdf_public(pdf_id, meta):
    return {
        "id": pdf_id,
        "filename": meta["filename"],
        "module": meta.get("module"),
        "weightage": meta.get("weightage", 0),
        "word_count": meta.get("word_count", 0),
        "page_count": meta.get("page_count", 0),
        "ocr_used": meta.get("ocr_used", False),
    }


@app.route("/api/pdfs", methods=["GET"])
def list_pdfs():
    return jsonify([_pdf_public(pid, m) for pid, m in PROJECT["pdfs"].items()])


@app.route("/api/ocr-languages", methods=["GET"])
def ocr_languages():
    return jsonify(available_languages())


@app.route("/api/pdfs", methods=["POST"])
def upload_pdfs():
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "No files uploaded."}), 400

    requested_langs = [
        l.strip().lower() for l in (request.form.get("languages") or "english").split(",") if l.strip()
    ]

    added = []
    errors = []
    for f in files:
        filename = (f.filename or "untitled").strip()
        lower = filename.lower()
        try:
            ocr_used = False
            if lower.endswith(".pdf"):
                raw_bytes = f.read()
                pages = extract_pages_from_pdf(io.BytesIO(raw_bytes))
                needs_ocr = (
                    not pages
                    or is_scanned_pdf(pages)
                    or any(len(page.strip()) < 120 for page in pages)
                )
                if needs_ocr:
                    try:
                        ocr_pages = ocr_pdf_bytes(raw_bytes, languages=requested_langs)
                        if any(p.strip() for p in ocr_pages):
                            # Keep the text layer where it is richer, but use
                            # OCR for image-heavy/short pages instead of
                            # silently dropping handwritten or scanned notes.
                            pages = [
                                (
                                    ocr_pages[index]
                                    if index < len(ocr_pages)
                                    and ocr_pages[index].strip()
                                    and len(raw_page.strip()) < 120
                                    else raw_page
                                )
                                for index in range(max(len(pages), len(ocr_pages)))
                                for raw_page in [pages[index] if index < len(pages) else ""]
                            ]
                            ocr_used = True
                        elif not pages:
                            errors.append(f"{filename}: this looks like a scanned PDF, but OCR could not "
                                          f"extract any text from it (poor scan quality or blank pages).")
                            continue
                    except OCRUnavailableError as e:
                        if not pages:
                            errors.append(
                                f"{filename}: this looks like a scanned/image-only PDF. {e} "
                                f"See README for the OCR install steps."
                            )
                            continue
                        # else: fall through and use whatever thin text-layer content exists
                clean = clean_pdf_pages(pages)
                page_count = len(pages)
            elif lower.endswith(".docx"):
                raw = extract_text_from_docx(f)
                clean = clean_plain_text(raw)
                page_count = None
            elif lower.endswith(".pptx"):
                slides = extract_pages_from_pptx(f)
                clean = clean_pdf_pages(slides)
                page_count = len(slides)
            elif lower.endswith(".txt"):
                raw = f.read().decode("utf-8", errors="ignore")
                clean = clean_plain_text(raw)
                page_count = None
            else:
                errors.append(f"{filename}: unsupported file type (use .pdf, .docx, .pptx or .txt).")
                continue

            minimum_upload_words = 8 if ocr_used else 30
            if not clean or len(clean.split()) < minimum_upload_words:
                errors.append(
                    f"{filename}: not enough extractable text after cleaning noise/metadata."
                )
                continue

            pdf_id = str(uuid.uuid4())
            default_module = "Module 1"
            PROJECT["pdfs"][pdf_id] = {
                "filename": filename,
                "clean_text": clean,
                "module": default_module,
                "weightage": 0,
                "word_count": len(clean.split()),
                "page_count": page_count,
                "ocr_used": ocr_used,
            }
            added.append(_pdf_public(pdf_id, PROJECT["pdfs"][pdf_id]))
        except Exception as e:
            traceback.print_exc()
            errors.append(f"{filename}: {e}")

    if added:
        _auto_configure_modules_for_pdfs()
        _rebalance_pdf_weightage()
        _clear_generated_paper()
        for pid in [a["id"] for a in added]:
            added_meta = PROJECT["pdfs"][pid]
            for a in added:
                if a["id"] == pid:
                    a["weightage"] = added_meta["weightage"]
                    a["module"] = added_meta["module"]

    return jsonify({"added": added, "errors": errors})


def _rebalance_pdf_weightage():
    """Give newly uploaded PDFs an equal-split default weightage so the
    total is always defined; the user can still edit weightage per PDF."""
    n = len(PROJECT["pdfs"])
    if n == 0:
        return
    share = round(100 / n, 2)
    for pid, meta in PROJECT["pdfs"].items():
        meta["weightage"] = share


def _effective_module_weightage():
    """Map each module to the combined weight of all PDFs assigned to it.
    This keeps the paper builder aligned with pdf-level importance instead of
    silently ignoring the weightage values entered by the user."""
    if not PROJECT["pdfs"]:
        return {m["name"]: float(m.get("weightage", 0) or 0) for m in PROJECT["modules"]}

    by_module = {}
    for meta in PROJECT["pdfs"].values():
        module_name = str(meta.get("module") or "").strip()
        if not module_name:
            continue
        try:
            weight = float(meta.get("weightage", 0) or 0)
        except (TypeError, ValueError):
            continue
        by_module[module_name] = by_module.get(module_name, 0.0) + weight

    total = sum(by_module.values())
    if not total:
        return {m["name"]: float(m.get("weightage", 0) or 0) for m in PROJECT["modules"]}

    derived = {name: (value / total) * 100.0 for name, value in by_module.items()}
    for module in PROJECT["modules"]:
        name = module.get("name")
        if name and name not in derived:
            derived[name] = 0.0
    return derived


def _sync_module_weights_to_pdfs():
    """Apply configured module weights to their PDFs, splitting shared modules evenly."""
    pdfs_by_module = {}
    for meta in PROJECT["pdfs"].values():
        pdfs_by_module.setdefault(meta.get("module"), []).append(meta)
    weights = {module["name"]: float(module.get("weightage", 0) or 0) for module in PROJECT["modules"]}
    for module_name, metas in pdfs_by_module.items():
        per_pdf_weight = weights.get(module_name, 0.0) / len(metas)
        for meta in metas:
            meta["weightage"] = per_pdf_weight


def _auto_configure_modules_for_pdfs():
    """Create one equal-weight module per uploaded PDF by default."""
    count = len(PROJECT["pdfs"])
    if not count:
        return
    names = []
    used_names = set()
    for meta in PROJECT["pdfs"].values():
        filename = meta.get("filename", "")
        name = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0].strip()
        name = name or f"Module {len(names) + 1}"
        base_name = name
        suffix = 2
        while name in used_names:
            name = f"{base_name} ({suffix})"
            suffix += 1
        names.append(name)
        used_names.add(name)
    share = round(100 / count, 2)
    PROJECT["modules"] = [
        {"name": name, "weightage": share} for name in names
    ]
    for index, meta in enumerate(PROJECT["pdfs"].values()):
        meta["module"] = names[index % count]


def _smart_module_weights_from_uploaded_material():
    """Distribute module weightage based on uploaded material volume, so the paper uses all material instead of overfitting to only the strongest source."""
    if not PROJECT["pdfs"]:
        return [{"name": "Module 1", "weightage": 100}]

    pdf_items = list(PROJECT["pdfs"].items())
    weights = []
    total_words = max(1, sum(max((meta.get("word_count") or 0), 1) for _, meta in pdf_items))
    names = []
    used_names = set()
    for _, meta in pdf_items:
        filename = meta.get("filename", "")
        name = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[0].strip()
        name = name or f"Module {len(names) + 1}"
        base_name = name
        suffix = 2
        while name in used_names:
            name = f"{base_name} ({suffix})"
            suffix += 1
        used_names.add(name)
        names.append(name)
        weights.append((meta.get("word_count") or 1) / total_words * 100)

    total = sum(weights)
    if total <= 0:
        share = round(100 / len(names), 2)
        return [{"name": n, "weightage": share} for n in names]

    out = []
    for name, raw_weight in zip(names, weights):
        out.append({"name": name, "weightage": round(raw_weight / total * 100, 2)})
    # normalize so the displayed total is exactly 100% with a stable final adjustment
    delta = round(100 - sum(item["weightage"] for item in out), 2)
    if out:
        out[0]["weightage"] = round(out[0]["weightage"] + delta, 2)
    return out


@app.route("/api/modules/auto", methods=["POST"])
def auto_modules_by_material():
    PROJECT["modules"] = _smart_module_weights_from_uploaded_material()
    for index, (pdf_id, meta) in enumerate(PROJECT["pdfs"].items()):
        module_name = PROJECT["modules"][index % len(PROJECT["modules"])]["name"]
        meta["module"] = module_name
    _clear_generated_paper()
    return jsonify(PROJECT["modules"])


@app.route("/api/pdfs/<pdf_id>", methods=["PATCH"])
def update_pdf(pdf_id):
    if pdf_id not in PROJECT["pdfs"]:
        return jsonify({"error": "PDF not found."}), 404
    data = request.get_json(force=True, silent=True) or {}
    if "module" in data:
        PROJECT["pdfs"][pdf_id]["module"] = data["module"]
    if "weightage" in data:
        try:
            PROJECT["pdfs"][pdf_id]["weightage"] = float(data["weightage"])
        except (TypeError, ValueError):
            return jsonify({"error": "weightage must be numeric."}), 400
    _clear_generated_paper()
    return jsonify(_pdf_public(pdf_id, PROJECT["pdfs"][pdf_id]))


@app.route("/api/pdfs/<pdf_id>", methods=["DELETE"])
def delete_pdf(pdf_id):
    PROJECT["pdfs"].pop(pdf_id, None)
    _auto_configure_modules_for_pdfs()
    _rebalance_pdf_weightage()
    _clear_generated_paper()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Modules & config
# ---------------------------------------------------------------------------
@app.route("/api/modules", methods=["GET"])
def get_modules():
    return jsonify(PROJECT["modules"])


@app.route("/api/modules", methods=["POST"])
def set_modules():
    data = request.get_json(force=True, silent=True) or {}
    modules = data.get("modules")
    if not isinstance(modules, list) or not modules:
        return jsonify({"error": "modules must be a non-empty list of {name, weightage}."}), 400
    try:
        names = [str(m["name"]).strip() for m in modules]
        weights = [float(m.get("weightage", 0)) for m in modules]
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Each module needs a name and numeric weightage."}), 400
    if not all(names) or len(set(names)) != len(names):
        return jsonify({"error": "Module names must be non-empty and unique."}), 400
    if not all(math.isfinite(weight) and weight >= 0 for weight in weights):
        return jsonify({"error": "Module weightages must be finite, non-negative numbers."}), 400
    total = sum(weights)
    if abs(total - 100) > 0.5:
        return jsonify({"error": f"Module weightage must total 100% (currently {total}%)."}), 400
    old_names = [m["name"] for m in PROJECT["modules"]]
    old_to_new = {
        old: names[index] for index, old in enumerate(old_names)
        if index < len(names)
    }
    PROJECT["modules"] = [{"name": name, "weightage": weight} for name, weight in zip(names, weights)]
    for index, meta in enumerate(PROJECT["pdfs"].values()):
        old_module = meta.get("module")
        meta["module"] = old_to_new.get(old_module, names[index % len(names)])
    _sync_module_weights_to_pdfs()
    _clear_generated_paper()
    return jsonify(PROJECT["modules"])


@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({
        "config": PROJECT["config"],
        "difficulty_mix": PROJECT["difficulty_mix"],
    })


@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json(force=True, silent=True) or {}
    if "difficulty_mix" in data:
        mix = data["difficulty_mix"]
        try:
            cleaned_mix = {str(k): float(v) for k, v in mix.items()}
        except (AttributeError, TypeError, ValueError):
            return jsonify({"error": "Difficulty weightage values must be numeric."}), 400
        if not all(math.isfinite(value) and value >= 0 for value in cleaned_mix.values()):
            return jsonify({"error": "Difficulty weightages must be finite, non-negative numbers."}), 400
        total = sum(cleaned_mix.values())
        if abs(total - 100) > 0.5:
            return jsonify({"error": f"Difficulty weightage must total 100% (currently {total}%)."}), 400
        PROJECT["difficulty_mix"] = cleaned_mix
    if "marks_scheme" in data:
        scheme = data["marks_scheme"]
        if not isinstance(scheme, list) or not scheme:
            return jsonify({"error": "marks_scheme must be a non-empty list of {marks, count}."}), 400
        try:
            cleaned = []
            for g in scheme:
                marks = int(g["marks"])
                count = int(g["count"])
                attempt = int(g["attempt"]) if g.get("attempt") not in (None, "") else count
                cleaned.append({"marks": marks, "count": count, "attempt": attempt})
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Each marks-scheme row needs numeric 'marks' and 'count' (and optional 'attempt')."}), 400
        if any(g["marks"] <= 0 or g["count"] <= 0 for g in cleaned):
            return jsonify({"error": "Marks and count must be positive numbers."}), 400
        if any(g["attempt"] <= 0 or g["attempt"] > g["count"] for g in cleaned):
            return jsonify({"error": "'Attempt' must be between 1 and the offered count for each row."}), 400
        PROJECT["config"]["marks_scheme"] = cleaned
    if "use_all_material" in data:
        PROJECT["config"]["use_all_material"] = bool(data["use_all_material"])
    for key in ("title", "subject", "duration", "education_level", "instructions"):
        if key in data:
            PROJECT["config"][key] = data[key]
    _clear_generated_paper()
    return jsonify({"config": PROJECT["config"], "difficulty_mix": PROJECT["difficulty_mix"]})


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
@app.route("/api/generate", methods=["POST"])
def generate():
    if not PROJECT["pdfs"]:
        return jsonify({"error": "Upload at least one PDF/DOCX/TXT before generating."}), 400

    with GENERATION_LOCK:
        if GENERATION_STATE.get("status") == "running":
            return jsonify({"error": "Question generation is already running."}), 409
        job_id = str(uuid.uuid4())
        GENERATION_STATE.clear()
        GENERATION_STATE.update({
            "job_id": job_id,
            "status": "running",
            "progress": 1,
            "message": "Starting generation",
            "result": None,
            "error": None,
        })

    worker = threading.Thread(target=_run_generation, args=(job_id,), daemon=True)
    worker.start()
    return jsonify({"job_id": job_id}), 202


@app.route("/api/generate/<job_id>", methods=["GET"])
def generation_status(job_id):
    with GENERATION_LOCK:
        if GENERATION_STATE.get("job_id") != job_id:
            return jsonify({"error": "Generation job not found."}), 404
        state = dict(GENERATION_STATE)
    return jsonify(state)


def _set_generation_state(job_id, **updates):
    with GENERATION_LOCK:
        if GENERATION_STATE.get("job_id") == job_id:
            GENERATION_STATE.update(updates)


def _run_generation(job_id):
    try:
        _run_generation_impl(job_id)
    except Exception as e:
        traceback.print_exc()
        _set_generation_state(
            job_id, status="failed", error=f"Question generation failed: {e}", error_status=500
        )


def _run_generation_impl(job_id):
    def report_progress(progress, message):
        _set_generation_state(
            job_id,
            progress=max(1, min(99, round(progress, 2))),
            message=message,
        )

    _clear_generated_paper()
    try:
        report_progress(3, "Preparing uploaded material")
        derived_module_weights = _effective_module_weightage()
        if derived_module_weights:
            PROJECT["modules"] = [
                {"name": name, "weightage": float(weight)}
                for name, weight in sorted(derived_module_weights.items())
            ]
        candidates, gen_stats, per_pdf_meta = build_candidate_bank(
            PROJECT["pdfs"],
            progress_callback=report_progress,
            education_level=PROJECT["config"].get("education_level", "General / Mixed"),
            use_all_material=PROJECT["config"].get("use_all_material", True),
        )
    except Exception as e:
        traceback.print_exc()
        _set_generation_state(
            job_id, status="failed", error=f"Content analysis failed: {e}", error_status=500
        )
        return

    if not candidates:
        _set_generation_state(
            job_id,
            status="failed",
            error=(
                "The app could not find enough valid source-grounded questions in the uploaded material. "
                "Please check OCR quality, add more explanatory course text, or reduce the paper requirements."
            ),
            error_status=400,
        )
        return

    report_progress(82, "Allocating questions to the marks scheme")
    builder = PaperBuilder(candidates, PROJECT["modules"], PROJECT["difficulty_mix"], PROJECT["config"])
    paper = builder.build()

    if not paper["questions"]:
        _set_generation_state(
            job_id,
            status="failed",
            error=("Generation produced zero valid questions after allocation. "
                   "Try reducing the counts in your marks scheme, or upload more source material."),
            error_status=400,
        )
        return

    report_progress(92, "Checking module coverage and paper totals")
    requested_by_marks = {}
    actual_by_marks = {}
    for row in paper["meta"].get("marks_scheme", []):
        marks = int(row["marks"])
        requested_by_marks[marks] = requested_by_marks.get(marks, 0) + int(row["count"])
    for question in paper["questions"]:
        marks = int(question.get("marks", 0))
        actual_by_marks[marks] = actual_by_marks.get(marks, 0) + 1

    missing_rows = [
        f"{marks}-mark row: {actual_by_marks.get(marks, 0)} of {requested} questions supported"
        for marks, requested in sorted(requested_by_marks.items())
        if actual_by_marks.get(marks, 0) < requested
    ]
    actual_marks = paper["meta"]["total_marks_actual"]
    target_marks = paper["meta"]["total_marks_target"]
    if paper["shortfalls"] or missing_rows or actual_marks != target_marks:
        details = " ".join(missing_rows) if missing_rows else "Some configured rows are unsupported by the uploaded material."
        _set_generation_state(
            job_id,
            status="failed",
            error=(
                f"A full paper could not be supported by the uploaded material: "
                f"{actual_marks} of {target_marks} target marks were grounded. "
                f"Add more source material or reduce the configured paper requirements. {details}"
            ),
            error_status=400,
        )
        return

    dashboard = compute_dashboard(
        paper, PROJECT["pdfs"], PROJECT["modules"], PROJECT["difficulty_mix"], gen_stats
    )

    for module in dashboard["module_analysis"]:
        if module["actual_marks"] + 0.01 < module["target_marks"]:
            paper["warnings"].append(
                f"{module['module']}: source coverage is {module['actual_marks']:g} of "
                f"{module['target_marks']:g} target marks; the remaining weight was "
                "allocated to other supported modules."
            )

    PROJECT["candidates"] = candidates
    PROJECT["gen_stats"] = gen_stats
    PROJECT["per_pdf_meta"] = per_pdf_meta
    PROJECT["paper"] = paper
    PROJECT["builder"] = builder
    _set_generation_state(
        job_id,
        status="completed",
        progress=100,
        message="Question paper ready",
        result={"paper": paper, "dashboard": dashboard},
    )


# ---------------------------------------------------------------------------
# Editable paper
# ---------------------------------------------------------------------------
def _require_paper():
    if not PROJECT["paper"]:
        return None
    return PROJECT["paper"]


def _renormalize_groups(questions):
    """After adding/deleting items, re-sync each choice group's offered/
    attempt counts and (a)/(b)/(c) labels to match who's actually left."""
    by_group = {}
    for q in questions:
        gid = q.get("group_id")
        if gid:
            by_group.setdefault(gid, []).append(q)
    for gid, items in list(by_group.items()):
        offered = len(items)
        attempt = min(items[0].get("group_attempt", offered) or offered, offered)
        for i, q in enumerate(items):
            q["group_offered"] = offered
            q["group_attempt"] = attempt
            q["group_label"] = _group_label(i)


def _renumber(questions):
    _renormalize_groups(questions)
    PaperBuilder._assign_numbers(questions)


def _recompute_marks(paper):
    paper["meta"]["total_marks_actual"] = compute_total_marks(paper["questions"])


@app.route("/api/paper", methods=["GET"])
def get_paper():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    return jsonify(paper)


@app.route("/api/paper", methods=["PUT"])
def update_paper():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    data = request.get_json(force=True, silent=True) or {}
    if "meta" in data:
        paper["meta"].update(data["meta"])
    if "questions" in data:
        paper["questions"] = data["questions"]
        _renumber(paper["questions"])
        _recompute_marks(paper)
    return jsonify(paper)


@app.route("/api/paper/questions", methods=["POST"])
def add_question():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    data = request.get_json(force=True, silent=True) or {}
    q = {
        "id": str(uuid.uuid4()),
        "type": data.get("type", "Explanation"),
        "topic": data.get("topic", ""),
        "question": data.get("question", ""),
        "answer": data.get("answer", ""),
        "module": data.get("module", PROJECT["modules"][0]["name"] if PROJECT["modules"] else "Module 1"),
        "source_pdf": data.get("source_pdf"),
        "source_chunk_idx": data.get("source_chunk_idx"),
        "bloom": data.get("bloom", "Understand"),
        "difficulty": data.get("difficulty", "Medium"),
        "marks": int(data.get("marks", 5)),
        "quality_score": 0,
    }
    group_id = data.get("group_id")
    if group_id:
        siblings = [x for x in paper["questions"] if x.get("group_id") == group_id]
        if siblings:
            q["marks"] = siblings[0]["marks"]  # alternatives in a group must carry equal marks
            q["group_id"] = group_id
    paper["questions"].append(q)
    _renumber(paper["questions"])
    _recompute_marks(paper)
    return jsonify(q)


@app.route("/api/paper/questions/<qid>", methods=["DELETE"])
def delete_question(qid):
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    paper["questions"] = [q for q in paper["questions"] if q["id"] != qid]
    _renumber(paper["questions"])
    _recompute_marks(paper)
    return jsonify({"ok": True})


@app.route("/api/paper/questions/<qid>/regenerate", methods=["POST"])
def regenerate_question(qid):
    paper = _require_paper()
    builder = PROJECT.get("builder")
    if not paper or not builder:
        return jsonify({"error": "No paper generated yet."}), 404
    idx = next((i for i, q in enumerate(paper["questions"]) if q["id"] == qid), None)
    if idx is None:
        return jsonify({"error": "Question not found."}), 404

    existing_ids = {q["id"] for q in paper["questions"]}
    new_q = builder.regenerate_one(paper["questions"][idx], exclude_ids=existing_ids)
    if new_q is None:
        return jsonify({"error": "No unused, distinct, source-supported replacement is available."}), 409
    paper["questions"][idx] = new_q
    _renumber(paper["questions"])
    _recompute_marks(paper)
    return jsonify(new_q)


@app.route("/api/paper/reorder", methods=["PUT"])
def reorder_questions():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    data = request.get_json(force=True, silent=True) or {}
    order = data.get("order") or []
    by_id = {q["id"]: q for q in paper["questions"]}
    new_list = [by_id[i] for i in order if i in by_id]
    # append anything missed (safety net)
    new_list += [q for q in paper["questions"] if q["id"] not in order]
    paper["questions"] = new_list
    _renumber(paper["questions"])
    return jsonify(paper)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    dashboard = compute_dashboard(
        paper, PROJECT["pdfs"], PROJECT["modules"], PROJECT["difficulty_mix"], PROJECT["gen_stats"]
    )
    return jsonify(dashboard)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@app.route("/api/export/paper/pdf", methods=["POST"])
def export_paper_pdf():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    try:
        buf = build_paper_pdf(paper)
        name = paper["meta"]["title"].replace(" ", "_") + ".pdf"
        return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=name)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"PDF export failed: {e}"}), 500


@app.route("/api/export/answerkey/pdf", methods=["POST"])
def export_answerkey_pdf():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    try:
        buf = build_answer_key_pdf(paper)
        name = paper["meta"]["title"].replace(" ", "_") + "_Answer_Key.pdf"
        return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=name)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Answer key PDF export failed: {e}"}), 500


@app.route("/api/export/paper/docx", methods=["POST"])
def export_paper_docx():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    try:
        buf = build_paper_docx(paper)
        name = paper["meta"]["title"].replace(" ", "_") + ".docx"
        return send_file(
            buf, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True, download_name=name,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"DOCX export failed: {e}"}), 500


@app.route("/api/export/answerkey/docx", methods=["POST"])
def export_answerkey_docx():
    paper = _require_paper()
    if not paper:
        return jsonify({"error": "No paper generated yet."}), 404
    try:
        buf = build_answer_key_docx(paper)
        name = paper["meta"]["title"].replace(" ", "_") + "_Answer_Key.docx"
        return send_file(
            buf, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            as_attachment=True, download_name=name,
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Answer key DOCX export failed: {e}"}), 500


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------
@app.route("/api/reset", methods=["POST"])
def reset_project():
    global PROJECT
    PROJECT = _new_project()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
