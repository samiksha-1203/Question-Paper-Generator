from app import _run_generation_impl, PROJECT, GENERATION_STATE, _clear_generated_paper
from paper_builder import PaperBuilder
from pipeline import build_candidates_for_pdf


def test_select_from_pool_uses_repeated_topic_when_needed():
    builder = PaperBuilder([], [], {"Easy": 50, "Medium": 50, "Hard": 0}, {"marks_scheme": [{"marks": 5, "count": 2}]})
    builder.selected_topics["data visualization"] = 1
    candidate = {
        "id": "q1",
        "module": "Module 1",
        "topic": "Data Visualization",
        "difficulty": "Medium",
        "type": "Definition",
        "_context_pool": ["A chart is a graph that helps people visualize data.", "A table organizes values clearly."]
    }

    chosen = builder._select_from_pool([candidate], 5, 1)

    assert chosen == [candidate]


def test_configured_module_weightages_are_applied_to_uploaded_pdfs():
    from app import _sync_module_weights_to_pdfs

    original_modules = PROJECT["modules"]
    original_pdfs = PROJECT["pdfs"]
    try:
        PROJECT["modules"] = [
            {"name": "Alpha", "weightage": 20},
            {"name": "Beta", "weightage": 80},
        ]
        PROJECT["pdfs"] = {
            "a": {"module": "Alpha", "weightage": 50},
            "b": {"module": "Beta", "weightage": 50},
        }

        _sync_module_weights_to_pdfs()

        assert [meta["weightage"] for meta in PROJECT["pdfs"].values()] == [20, 80]
    finally:
        PROJECT["modules"] = original_modules
        PROJECT["pdfs"] = original_pdfs


def test_build_candidates_for_pdf_falls_back_when_ollama_returns_no_valid_questions(monkeypatch):
    monkeypatch.setattr("pipeline.chunk_text", lambda text: ["This course explains the use of neural networks in image recognition."])
    monkeypatch.setattr("pipeline.is_devanagari_majority", lambda text: False)
    monkeypatch.setattr("pipeline.os.environ.get", lambda key, default=None: "qwen3:1.7b")
    monkeypatch.setattr("pipeline.extract_concepts", lambda chunks, pdf_id, module: [{"name": "Neural Network", "support": [{"module": module, "pdf_id": pdf_id}]}])
    monkeypatch.setattr("pipeline.generate_question_bank", lambda concepts: ([{
        "id": "fallback-1",
        "module": "Module 1",
        "topic": "Neural Network",
        "question": "Define neural network.",
        "answer": "It is a computational model inspired by biological neural systems.",
        "type": "Definition",
        "difficulty": "Easy",
        "_context_pool": ["A neural network is a computational model inspired by biological neural systems."]
    }], {"total_candidates": 1, "accepted": 1, "rejected_invalid": 0, "duplicates_removed": 0}))
    monkeypatch.setattr("pipeline.generate_source_questions", lambda *args, **kwargs: ([], {
        "total_candidates": 0, "accepted": 0, "rejected_invalid": 0, "duplicates_removed": 0,
        "question_writer": "local Ollama"
    }))

    candidates, stats, _, _ = build_candidates_for_pdf("pdf-1", "This course explains neural networks.", "Module 1")

    assert len(candidates) == 1
    assert candidates[0]["topic"] == "Neural Network"
    assert stats["accepted"] == 1


def test_generation_rejects_incomplete_paper(monkeypatch):
    GENERATION_STATE.clear()
    GENERATION_STATE.update({"job_id": "job-1", "status": "running", "progress": 1, "message": "Starting generation", "result": None, "error": None})
    _clear_generated_paper()
    PROJECT["modules"] = [{"name": "Module 1", "weightage": 100}]
    PROJECT["difficulty_mix"] = {"Easy": 50, "Medium": 50, "Hard": 0}
    PROJECT["config"] = {
        "title": "Test",
        "subject": "Subject",
        "duration": "120 minutes",
        "education_level": "General / Mixed",
        "marks_scheme": [{"marks": 5, "count": 2}],
        "instructions": ["Write clearly."],
    }
    PROJECT["pdfs"] = {
        "pdf-1": {"filename": "test.pdf", "module": "Module 1", "weightage": 100, "clean_text": "Neural network is a model.", "word_count": 8, "page_count": 1, "ocr_used": False}
    }
    candidate = {
        "id": "q1",
        "module": "Module 1",
        "topic": "Neural Network",
        "question": "Define neural network.",
        "answer": "It is a computational model inspired by biological neural systems.",
        "type": "Definition",
        "difficulty": "Easy",
        "_context_pool": ["A neural network is a computational model inspired by biological neural systems."],
        "source_pdf": "pdf-1",
        "source_chunk_idx": 0,
        "quality_score": 3,
    }

    monkeypatch.setattr("app.build_candidate_bank", lambda *args, **kwargs: ([candidate], {"total_candidates": 1, "accepted": 1, "rejected_invalid": 0, "duplicates_removed": 0}, {"pdf-1": {"n_chunks": 1, "n_concepts": 1, "total_candidates": 1, "accepted": 1, "rejected_invalid": 0, "duplicates_removed": 0}}))
    monkeypatch.setattr("app.compute_dashboard", lambda *args, **kwargs: {"module_analysis": [{"module": "Module 1", "target_marks": 10, "actual_marks": 5}], "quality_metrics": {"question_writer": "test"}})

    partial = {"meta": {"total_marks_target": 10, "total_marks_actual": 5, "marks_scheme": [{"marks": 5, "count": 2}], "education_level": "General / Mixed"}, "questions": [{"id": "q1", "module": "Module 1", "difficulty": "Easy", "type": "Definition", "marks": 5, "question": "Define neural network.", "answer": "It is a computational model inspired by biological neural systems.", "group_id": None, "group_attempt": 1, "group_offered": 1}], "warnings": [], "shortfalls": [{"module": "configured modules", "marks": 5, "requested": 2, "found": 1, "note": "partial"}] }

    monkeypatch.setattr("app.PaperBuilder.build", lambda self: partial)

    _run_generation_impl("job-1")

    assert GENERATION_STATE["status"] == "failed"
    assert "A full paper could not be supported" in GENERATION_STATE["error"]


def test_generation_passes_use_all_material_preference_to_pipeline(monkeypatch):
    GENERATION_STATE.clear()
    GENERATION_STATE.update({"job_id": "job-2", "status": "running", "progress": 1, "message": "Starting generation", "result": None, "error": None})
    _clear_generated_paper()
    PROJECT["modules"] = [{"name": "Module 1", "weightage": 100}]
    PROJECT["difficulty_mix"] = {"Easy": 100, "Medium": 0, "Hard": 0}
    PROJECT["config"] = {
        "title": "Test",
        "subject": "Subject",
        "duration": "120 minutes",
        "education_level": "General / Mixed",
        "use_all_material": True,
        "marks_scheme": [{"marks": 5, "count": 2}],
        "instructions": ["Write clearly."],
    }
    PROJECT["pdfs"] = {
        "pdf-1": {"filename": "alpha.pdf", "module": "Module 1", "weightage": 50, "clean_text": "Alpha text ", "word_count": 200, "page_count": 1, "ocr_used": False},
        "pdf-2": {"filename": "beta.pdf", "module": "Module 1", "weightage": 50, "clean_text": "Beta text " * 5, "word_count": 800, "page_count": 1, "ocr_used": False},
    }

    captured = {}
    candidate = {
        "id": "q1",
        "module": "beta",
        "topic": "Neural Network",
        "question": "Define neural network.",
        "answer": "It is a computational model inspired by biological neural systems.",
        "type": "Definition",
        "difficulty": "Easy",
        "_context_pool": ["A neural network is a computational model inspired by biological neural systems."],
        "source_pdf": "pdf-2",
        "source_chunk_idx": 0,
        "quality_score": 3,
    }

    def fake_build_candidate_bank(*args, **kwargs):
        captured["use_all_material"] = kwargs.get("use_all_material")
        return ([candidate], {"total_candidates": 1, "accepted": 1, "rejected_invalid": 0, "duplicates_removed": 0}, {"pdf-1": {"n_chunks": 1, "n_concepts": 1, "total_candidates": 1, "accepted": 1, "rejected_invalid": 0, "duplicates_removed": 0}, "pdf-2": {"n_chunks": 1, "n_concepts": 1, "total_candidates": 1, "accepted": 1, "rejected_invalid": 0, "duplicates_removed": 0}})

    partial = {"meta": {"total_marks_target": 10, "total_marks_actual": 5, "marks_scheme": [{"marks": 5, "count": 2}], "education_level": "General / Mixed"}, "questions": [{"id": "q1", "module": "beta", "difficulty": "Easy", "type": "Definition", "marks": 5, "question": "Define neural network.", "answer": "It is a computational model inspired by biological neural systems.", "group_id": None, "group_attempt": 1, "group_offered": 1}], "warnings": [], "shortfalls": [] }

    monkeypatch.setattr("app.build_candidate_bank", fake_build_candidate_bank)
    monkeypatch.setattr("app.PaperBuilder.build", lambda self: partial)
    monkeypatch.setattr("app.compute_dashboard", lambda *args, **kwargs: {"module_analysis": [{"module": "beta", "target_marks": 10, "actual_marks": 5}], "quality_metrics": {"question_writer": "test"}})

    _run_generation_impl("job-2")

    assert captured["use_all_material"] is True
