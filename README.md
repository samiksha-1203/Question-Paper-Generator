# NLP Question Paper Generator

A local Flask application that extracts teaching material from PDF, DOCX,
PPTX, and TXT files, generates source-grounded exam questions with a local
Ollama model, allocates them to a configured marks scheme, and exports the
question paper and answer key as PDF or DOCX.

The repository contains application code and tests, not a validated research
dataset or published experimental results. See [Research-paper guidance](#research-paper-guidance)
for a reproducible way to evaluate it without presenting unmeasured claims.

## Features

- Multiple PDF, DOCX, PPTX, and TXT uploads; modules are initially created
  from filenames and given equal weights.
- PDF text extraction with cleanup and paragraph-style chunking. Scanned or
  image-heavy PDF pages can use Tesseract OCR.
- Local question generation through Ollama, defaulting to `qwen3:1.7b`.
  Candidates are checked for question validity, evidence citations, and
  duplication. If the model produces no usable candidates for a file, the
  concept/template pipeline is attempted as a fallback.
- Hindi/Marathi OCR when the matching Tesseract language packs are installed;
  Hindi/Marathi question generation is requested from Ollama for Devanagari
  majority text. A script-agnostic template path is used when model generation
  is explicitly disabled.
- Editable modules and difficulty weightages. **Auto-weight by material
  volume** uses extracted word counts as an initial allocation heuristic; it
  does not use the language model to judge module importance.
- **Use all uploaded material** is enabled by default. In Ollama mode this
  processes all extracted source units instead of sampling up to 32 units per
  file. Disable it to use an evenly sampled subset for faster processing.
- Marks scheme rows define marks per question, number offered, and number to
  attempt. Choice rows are rendered as a single choice group.
- Full-paper policy: generation fails instead of accepting an incomplete
  configured marks scheme. The app does not invent questions to fill gaps.
  Module targets can still be redistributed to other supported modules when
  the configured per-module supply is insufficient; check the dashboard.
- Marks-appropriate answers, editable questions and instructions, dashboard
  analytics, source attribution, and PDF/DOCX export of both paper and key.

## Requirements

- Python 3.10 or newer is recommended.
- Ollama and an installed chat model for model-first generation. The default
  is `qwen3:1.7b`.
- Tesseract OCR and Poppler are optional for scanned/image-heavy PDF OCR.
  Install Tesseract language data for each OCR language you need.
- Network access may be needed once for Python packages, Ollama model download,
  and automatic NLTK data download.

Dependencies declared by the project are in
[`backend/requirements.txt`](backend/requirements.txt). NLTK resources are
checked at runtime and downloaded if missing.

## Installation

Run commands from the repository root (the directory containing `backend/`).

### Windows PowerShell

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks activation for this terminal, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

NLTK downloads missing data automatically on startup. To pre-download it while
the environment is activated:

```bash
python -c "import nltk; [nltk.download(p, quiet=True) for p in ['punkt','punkt_tab','averaged_perceptron_tagger','averaged_perceptron_tagger_eng','wordnet','omw-1.4','stopwords']]"
```

## Ollama setup

Install Ollama from <https://ollama.com/download>, then download the default
model once:

```powershell
ollama pull qwen3:1.7b
ollama run qwen3:1.7b "hello"
```

Ollama for Windows normally runs as a background application. If the service
is not running, start it in a separate terminal with `ollama serve`; keep that
terminal open while using the app. Do not start a second server if Ollama is
already running. `ollama run ... "hello"` is a connectivity/model check, not
the command that starts the web app.

Optional environment settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_MODEL` | `qwen3:1.7b` | Select an installed Ollama model. |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Change the Ollama server URL. |
| `PORT` | `5000` | Change the Flask server port. |
| `FLASK_DEBUG` | `0` | Set to `1` for Flask development reload/debug mode. |

Set `OLLAMA_MODEL=off` to use the template-based generation path without
Ollama. The larger `qwen3:8b` model can be selected if installed, but uses
more memory and is generally slower. Ollama receives extracted text, not page
images; it cannot infer visual relationships in diagrams.

## OCR setup (optional)

OCR requires both Tesseract and Poppler. English, Hindi, and Marathi are
available only when their Tesseract language data is installed. The app
attempts OCR when PDF text extraction is sparse or pages appear image-heavy;
OCR processing is limited to the first 60 pages per PDF.

**Ubuntu/Debian:**

```bash
sudo apt-get install -y tesseract-ocr poppler-utils
sudo apt-get install -y tesseract-ocr-hin tesseract-ocr-mar
```

**macOS (Homebrew):**

```bash
brew install tesseract tesseract-lang poppler
```

**Windows:** install Tesseract from
[UB-Mannheim](https://github.com/UB-Mannheim/tesseract/wiki) and Poppler from
[the Windows builds](https://github.com/oschwartz10612/poppler-windows/releases).
Add their executable directories to `PATH`. Select the needed language data
during Tesseract setup.

## Run

With the Python virtual environment activated and Ollama running:

```powershell
cd backend
python app.py
```

Open <http://localhost:5000>. On macOS/Linux, activate with
`source .venv/bin/activate` from `backend/` and run `python app.py` there.
Uploaded files and generated work are held in memory and are cleared when the
server restarts or **New Project** is used. This is a local, single-user app;
it is not configured as a production multi-user service.

## Workflow

1. Upload one or more supported files. PDFs, DOCX, PPTX, and TXT are accepted;
   the total request limit is 60 MB.
2. Review the filename-derived modules. Use **Auto-weight by material volume**
   for weights proportional to extracted word counts, or edit the weights
   manually. Module weights and difficulty percentages must each total 100%.
3. Choose education level, paper metadata, marks rows, and whether to process
   all extracted source units. More source units can increase Ollama runtime.
4. Generate. A full configured paper is required; if enough valid candidates
   cannot be allocated, the app reports a failure instead of saving a partial
   paper. Check the upload text/OCR, source content, model output, and marks
   requirements when this happens.
5. Edit questions, answers, marks, modules, difficulty, ordering, and
   instructions. Question regeneration selects a replacement from the existing
   candidate bank; it does not make a new Ollama request.
6. Review module, difficulty, PDF-contribution, marks, and generation-quality
   metrics in the dashboard.
7. Export the question paper and answer key in PDF or DOCX format.

### Extraction and quality notes

- PDF extraction is text-layer based first. OCR helps with scanned pages but
  can introduce recognition errors; inspect the uploaded word count and OCR
  quality when questions are missing.
- DOCX extraction reads paragraph text. Tables and embedded images are not
  extracted as course text. PPTX extraction reads slide text, not text inside
  images or diagram relationships.
- Cleanup removes common page numbers, repeated lines, and metadata-like
  content. Review source files if meaningful content appears to have been
  removed during extraction.
- No subject allowlist is used. A topic is not excluded because it is GDPR,
  data visualization, or another subject. Question candidates can still fail
  validation if they lack usable extracted evidence, are malformed, or are
  duplicates.
- Long-answer slots require more distinct source sentences than short-answer
  slots. In the current allocator, 8+ marks require at least three distinct
  context sentences; 5-7 marks require two; lower-mark questions require one.
- All-material mode means every extracted source unit is presented for
  generation; it does not guarantee a question from every page or module. The
  model and validation pipeline may produce no acceptable question for some
  units. The full marks scheme can still fail if candidate supply is
  insufficient.

## Research-paper guidance

This repository can support a study of source-grounded question generation,
but it does not establish that the generated questions are educationally
valid or better than a baseline. Treat the following as an evaluation plan,
not reported results.

Possible research question: *How effectively can a local language model
generate source-grounded, marks-aware examination questions from heterogeneous
course materials?*

Suggested paper structure:

1. **Abstract:** problem, method, evaluation dataset, key measured results, and
   limitations. Write this after running the evaluation.
2. **Introduction:** educational assessment use case, scope, contributions,
   and research questions.
3. **Related work:** question generation, retrieval/grounding, OCR for learning
   materials, and automated assessment. Cite and compare real published work.
4. **System and method:** input formats, text/OCR extraction, chunking,
   question-generation model/version, evidence validation, deduplication,
   marks allocation, fallback behavior, and answer construction.
5. **Experimental setup:** describe the course materials, licenses/permissions,
   languages, page counts, OCR status, model and quantization if known,
   hardware, software versions, prompts/configuration, and baseline systems.
6. **Evaluation:** have qualified reviewers assess factual correctness,
   relevance to the source, clarity, answerability, difficulty, and marks fit.
   Report question acceptance rate, evidence-grounding rate, duplicate rate,
   paper completion rate, OCR error where reference transcripts exist, and
   generation time/resource use.
7. **Reliability:** use independent raters, define the rubric before rating,
   report agreement (for example Cohen's kappa for two raters where suitable),
   and adjudicate disagreements. Split data by course/source to avoid leakage.
8. **Results and discussion:** report measured values, uncertainty and failure
   cases; compare baselines under the same inputs and marks scheme.
9. **Ethics, privacy, and limitations:** document consent/licensing, local
   processing, model limitations, OCR errors, unsupported diagrams, language
   coverage, and risks of using generated material without teacher review.
10. **Conclusion and reproducibility:** summarize only supported findings and
    provide anonymized evaluation data, rubric, prompts, configuration, model
    identifiers, and commands where rights permit.

Do not describe dashboard values as educational-quality scores unless the
metric has been validated for that purpose. `quality_score` and dashboard
counts are implementation diagnostics, not a substitute for human evaluation.
Do not publish private teaching materials, student information, or copyrighted
content without appropriate permission. Keep an evaluation log outside the
app: the project store is in-memory and does not persist experiments.

## Tests

From `backend/` with the project virtual environment activated:

```bash
python -m pip install pytest
python -m pytest -q
```

Tests cover selected source-generation, allocation, fallback, and full-paper
behaviors. They do not constitute a human evaluation of question quality.

## Project layout

```text
backend/
  app.py                    Flask API, upload workflow, in-memory project state
  text_extraction.py        PDF/DOCX/PPTX/TXT extraction, cleanup, chunking
  ocr_extraction.py         Tesseract OCR and language discovery
  nlp_utils.py              NLTK bootstrap, tokenization, text similarity
  concept_extraction.py     English concept extraction
  question_generator.py    Template candidates, validation, deduplication, answers
  multilingual_pipeline.py Hindi/Marathi template extraction path
  ollama_question_writer.py Ollama prompts, citations, validation, fallback wording
  pipeline.py               Per-file and aggregate candidate generation
  paper_builder.py          Marks allocation, paper building, dashboard analytics
  pdf_export.py             PDF question-paper and answer-key export
  docx_export.py            DOCX question-paper and answer-key export
  templates/index.html      Six-step browser interface
  static/css/style.css      Application styling
  static/js/app.js          Browser workflow and dashboard
  static/js/vendor/         Locally vendored Chart.js
  requirements.txt          Python package dependencies
  test_*.py                 Backend regression tests
```
