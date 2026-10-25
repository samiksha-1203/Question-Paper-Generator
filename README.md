# NLP Question Paper Generator

## Abstract

The NLP Question Paper Generator is a local Flask-based application designed to convert uploaded academic material into a structured, source-grounded question paper. The system accepts PDF, DOCX, PPTX, and TXT files, extracts text from them, cleans and chunks the content, identifies meaningful concepts, generates exam-style questions, validates them against source evidence, and allocates them according to a configurable marks scheme. It also exports the final paper and answer key as PDF or DOCX files.

The project combines rule-based NLP methods, local language-model generation through Ollama, and a marks-aware paper assembly pipeline. It is intended for educational scenarios where teachers want a quick and transparent way to generate exam questions from existing teaching material without exposing data to external cloud services.

---

## Problem Statement

Manual preparation of question papers is time-consuming, repetitive, and dependent on teacher expertise. In many educational settings, instructors must read large documents, identify important topics, and construct balanced question papers with appropriate difficulty and marks distribution.

This project addresses that challenge by automating the process of:

- extracting content from teaching resources,
- identifying key concepts and source-grounded topics,
- generating valid question candidates,
- validating them against the source material,
- organizing them into a complete marks-based exam paper.

---

## Objectives

1. To design a system that processes heterogeneous teaching materials including PDF, DOCX, PPTX, and TXT files.
2. To clean and structure raw text for educational question generation.
3. To detect important concepts and build a candidate question bank from the source material.
4. To use a local language model (Ollama) for source-grounded question wording when available.
5. To validate generated questions using source evidence, duplication checks, and structural quality rules.
6. To allocate questions according to module weightage, difficulty mix, and marks scheme.
7. To export the final paper and answer key in a printable format.

---

## Scope

The application focuses on local, single-user academic assessment generation. It is not designed as a multi-user production SaaS platform. The project is intended primarily for educational institutions, teachers, and researchers evaluating AI-assisted paper generation in a controlled local environment.

The system supports:

- PDF, DOCX, PPTX, and TXT input
- English and Devanagari-based material handling
- Optional OCR for scanned or image-heavy PDFs
- Local LLM-based generation via Ollama
- Template fallback when the model is unavailable or produces no valid content
- Marks-driven question paper generation and export

---

## Tools and Technologies

- Python 3.10+
- Flask
- Ollama
- qwen3:1.7b (default local model)
- Tesseract OCR
- Poppler
- Python libraries:
  - Flask
  - pypdf
  - python-docx
  - reportlab
  - nltk
  - pytesseract
  - pdf2image

---

## System Architecture

The project follows a modular pipeline architecture.

### Core components

- `backend/app.py` - Flask application and web interface
- `backend/text_extraction.py` - extraction and normalization of PDF/DOCX/PPTX/TXT resources
- `backend/ocr_extraction.py` - OCR handling for scanned documents
- `backend/concept_extraction.py` - concept identification and sentence filtering
- `backend/question_generator.py` - template-based question generation and validation
- `backend/ollama_question_writer.py` - Ollama-based question generation and source-grounded validation
- `backend/pipeline.py` - end-to-end candidate generation per file and aggregate candidate bank construction
- `backend/paper_builder.py` - marks allocation, difficulty balancing, and exam assembly
- `backend/pdf_export.py` and `backend/docx_export.py` - PDF/DOCX output generation
- `backend/templates/index.html` - frontend UI
- `backend/static/js/app.js` - browser-side workflow logic

---

## Methodology

### 1. Input acquisition and file handling

The user uploads one or more study files through the web interface. Supported formats include:

- PDF
- DOCX
- PPTX
- TXT

The application stores the uploaded material in an in-memory project state, which is cleared when the app restarts or a new project is created. Uploaded files are assigned to modules and weights before question generation begins.

### 2. Text extraction

The extraction module reads the file content and converts it into a clean text format. For PDF documents, text is extracted from the text layer first. For scanned or image-heavy PDFs, OCR is triggered as a secondary step. DOCX and PPTX extraction reads textual content from paragraphs and slide text, while TXT files are processed directly.

### 3. Preprocessing and cleanup

Raw extracted text often contains page numbers, repeated lines, formatting artifacts, and metadata-like noise. The preprocessing pipeline removes common artifacts and normalizes the content so that sentence segmentation and concept extraction operate on cleaner input.

This stage is important because invalid or noisy source text can produce poor question candidates and unreliable answers.

### 4. Chunking and segmentation

The cleaned text is divided into smaller chunks for finer-grained analysis. Chunk segmentation helps the system:

- prevent excessive context length,
- isolate topic-specific content,
- identify recurring concepts across sections,
- maintain traceability to the originating source chunk.

### 5. Concept extraction

The concept extraction module is designed to avoid weak or misleading question topics. It does not simply pick random nouns from a sentence. Instead, it filters content through a conservative pipeline:

- sentences are classified as fragment, illustrative, or substantive,
- illustrative and example-heavy sentences are filtered out for concept nomination,
- candidate phrases must be meaningful noun phrases,
- generic or meta words such as `example`, `topic`, `page`, `figure`, and `question` are rejected,
- common names and trivial entities are filtered to avoid irrelevant person-example questions,
- candidate concepts must recur across material to be considered valid.

This reduces the risk of generating questions about incidental examples rather than genuine syllabus concepts.

### 6. Local language model question generation

When Ollama is available, the app uses the default model:

```bash
qwen3:1.7b
```

The system sends source-grounded excerpts to the local model and requests exam-style question candidates. The model is asked to generate content that is aligned with the educational material, not random generic questions.

The project supports:

- model-first generation when Ollama is active,
- template-based fallback when the model is disabled or returns invalid output,
- multilingual handling through alternate generation flows for Devanagari-majority text.

### 7. Validation and quality control

Generated candidates are not accepted immediately. The system applies validation checks to ensure better question quality:

- question validity check,
- source grounding check,
- duplicate elimination,
- citation/evidence compatibility,
- answer support verification,
- compatibility with the target marks range.

The goal is to reject weak or fabricated content before it reaches the paper builder.

### 8. Marks-aware paper construction

After candidate generation, the project assembles a final question paper using the configured marks scheme. Each marks row includes:

- marks per question,
- total number of questions offered,
- number to attempt.

The system uses:

- module weightage,
- difficulty balance,
- question type appropriateness,
- source sufficiency checks for high-mark questions,
- final assembly into sections and choice groups.

This ensures that the final paper is not just a random collection of questions but a marks-balanced exam paper.

### 9. Export and output generation

Once the question paper has been assembled, it can be exported as:

- PDF question paper
- PDF answer key
- DOCX question paper
- DOCX answer key

The export layer uses the generated final question data and formatting rules to produce downloadable files.

---

## Model and Algorithm Summary

The system combines rule-based computational linguistics with local generative AI.

### Rule-based NLP components

- text cleaning
- heading and sentence detection
- noun phrase recognition
- concept relevance scoring
- duplication control
- difficulty heuristic estimation
- source sufficiency checks

### Generative component

- local model: `qwen3:1.7b`
- accessed through Ollama
- source-grounded content generation
- fallback template generation when needed

### Paper assembly component

- marks mapping
- module weighting
- difficulty balancing
- option / choice group generation
- final export preparation

---

## Algorithm Workflow

1. Upload source material.
2. Extract text and detect OCR needs.
3. Clean and chunk text.
4. Identify candidate concepts from substantive content.
5. Generate question candidates from those concepts.
6. Validate source grounding, duplicates, and quality.
7. Store a candidate bank with metadata (module, topic, source chunk, difficulty, type, marks range).
8. Select and arrange questions according to final marks scheme.
9. Build question paper and answer key.
10. Export final output in PDF or DOCX.

---

## Requirements

### Software requirements

- Python 3.10 or newer
- Ollama installed and running
- Optional OCR dependencies for scanned PDFs

### Python dependencies

The project dependencies are declared in:

```bash
backend/requirements.txt
```

The file includes:

- Flask
- pypdf
- python-docx
- reportlab
- nltk
- pytesseract
- pdf2image

---

## Installation

Run the following commands from the repository root.

### Windows PowerShell

```powershell
cd project/backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If execution policy blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
cd project/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Optional NLTK data download:

```bash
python -c "import nltk; [nltk.download(p, quiet=True) for p in ['punkt','punkt_tab','averaged_perceptron_tagger','averaged_perceptron_tagger_eng','wordnet','omw-1.4','stopwords']]"
```

---

## Ollama Setup

Install Ollama from the official site and pull the default model:

```powershell
ollama pull qwen3:1.7b
ollama run qwen3:1.7b "hello"
```

If Ollama is not already running, start it with:

```powershell
ollama serve
```

The application reads the model name from the environment variable `OLLAMA_MODEL`, which defaults to `qwen3:1.7b`.

Optional environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OLLAMA_MODEL` | `qwen3:1.7b` | Select installed Ollama model |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `PORT` | `5000` | Flask server port |
| `FLASK_DEBUG` | `0` | Debug mode |

Setting `OLLAMA_MODEL=off` disables local model generation and uses the template-based fallback pipeline.

---

## OCR Setup (Optional)

OCR is optional but useful for scanned or image-heavy PDFs.

### Ubuntu / Debian

```bash
sudo apt-get install -y tesseract-ocr poppler-utils
sudo apt-get install -y tesseract-ocr-hin tesseract-ocr-mar
```

### macOS

```bash
brew install tesseract tesseract-lang poppler
```

### Windows

Install Tesseract from the UB-Mannheim Tesseract build and Poppler from the Windows binaries, then add both directories to `PATH`.

---

## Running the Application

From the backend folder:

```bash
cd project/backend
python app.py
```

Open the application in a browser:

```text
http://localhost:5000
```

The app is designed as a local single-user application. Uploaded files and generated work exist in memory and are cleared when the app restarts or a new project is created.

---

## User Workflow

1. Upload course material in PDF, DOCX, PPTX, or TXT format.
2. Assign or review modules and weightage.
3. Set difficulty mix and marks configuration.
4. Generate candidate questions.
5. Review generated content and edit as needed.
6. Inspect dashboard analytics.
7. Export the final question paper and answer key.

---

## Evaluation and Quality Notes

This project does not claim full academic validation of generated question quality. Instead, it uses a combination of:

- source-grounded validation,
- duplicate filtering,
- marks-fit checks,
- module and difficulty balancing,
- administrative diagnostic metrics.

The built-in dashboard provides useful generation statistics, but these are implementation diagnostics rather than formal educational quality scores. Human review remains necessary for publishing or classroom use.

---

## Limitations

- This is a local, single-user application rather than a production multi-user system.
- OCR may introduce recognition errors for poor-quality scanned PDFs.
- Diagram-heavy or image-based material is not fully interpreted visually.
- Generated questions still require expert review before final academic use.
- The project does not provide a large benchmark dataset for rigorous academic evaluation.

---

## Future Scope

- add teacher review workflow and approval interface,
- support more languages and OCR models,
- improve source-grounding scoring and question filtering,
- integrate database storage for persistent projects,
- add user authentication and multi-user collaboration,
- create a formal benchmark and evaluation rubric for quality assessment.

---

## Project Structure

```text
project/
+-- README.md
+-- backend/
¦   +-- app.py
¦   +-- text_extraction.py
¦   +-- ocr_extraction.py
¦   +-- nlp_utils.py
¦   +-- concept_extraction.py
¦   +-- question_generator.py
¦   +-- multilingual_pipeline.py
¦   +-- ollama_question_writer.py
¦   +-- pipeline.py
¦   +-- paper_builder.py
¦   +-- pdf_export.py
¦   +-- docx_export.py
¦   +-- requirements.txt
¦   +-- templates/
¦   +-- static/
¦   +-- test_*.py
+-- .venv/
```

---

## Conclusion

The NLP Question Paper Generator demonstrates an end-to-end approach for converting educational materials into structured exam papers using local NLP and language-model techniques. The system is designed to make question generation more efficient, source-aware, and marks-oriented, while keeping the workflow local and transparent.

This project is especially relevant in educational environments where teachers need an AI-assisted but controlled method for generating assessment papers without deploying content to external cloud services.

---

## Notes

This repository contains the application code and tests rather than a validated experimental dataset or published research benchmark. It is intended as a practical system for local educational use and further research evaluation.
