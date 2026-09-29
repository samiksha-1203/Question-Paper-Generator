from ollama_question_writer import _decode_json_response, _generation_source_units, _is_grounded_in_citations


def test_decode_json_response_extracts_json_from_fenced_text():
    raw = '''Here is the result:
```json
{"questions":[{"topic":"Convolution","question":"What is convolution?","answer":"It detects local patterns.","type":"Definition","difficulty":"Easy","evidence_ids":["c0s0"]}]}
```
'''

    result = _decode_json_response(raw)

    assert result["questions"][0]["topic"] == "Convolution"
    assert result["questions"][0]["type"] == "Definition"


def test_grounding_uses_only_cited_source_sentences():
    cited = ["Predictive analytics uses historical data to predict future outcomes."]

    assert _is_grounded_in_citations(
        "Predictive Analytics",
        "What does predictive analytics predict?",
        "It uses historical data to predict future outcomes.",
        cited,
    )
    assert not _is_grounded_in_citations(
        "Predictive Analytics",
        "What does predictive analytics predict about profits?",
        "It uses historical data to predict future outcomes.",
        cited,
    )
    assert not _is_grounded_in_citations(
        "Predictive Analytics",
        "What does predictive analytics predict?",
        "It guarantees perfect accuracy.",
        cited,
    )


def test_use_all_material_processes_every_source_unit(monkeypatch):
    import ollama_question_writer as writer

    monkeypatch.setattr(writer, "MAX_SOURCE_UNITS_PER_PDF", 1)
    long_sentence = " ".join(["course"] * 181) + "."
    chunks = [long_sentence, long_sentence, long_sentence]

    assert len(_generation_source_units(chunks, True)) == 3
    assert len(_generation_source_units(chunks, False)) == 1
