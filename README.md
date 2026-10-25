# NLP Question Paper Generator

## Windows Installation and Run Guide

Follow these steps in order. All commands below are for **Windows PowerShell**.

### Step 1: Install Python

1. Download and install Python 3.10 or newer from
   [python.org/downloads](https://www.python.org/downloads/).
2. During installation, select **Add python.exe to PATH** if the installer
   offers that option.
3. Open PowerShell and verify that Python is installed:

   ```powershell
   py -3 --version
   ```

   The version shown must be Python 3.10 or newer. If `py` is not recognized,
   rerun the Python installer and enable the Python launcher/PATH options.

### Step 2: Download the project

1. Open the project repository on
   [GitHub](https://github.com/samiksha-1203/Question-Paper-Generator).
2. Select **Code**, then **Download ZIP**.
3. In File Explorer, extract the ZIP file.
4. Open the extracted project folder. It is the folder containing this
   `README.md` and the `backend` folder.

If you already have the project folder, use that folder instead of downloading
it again.

### Step 3: Open PowerShell in the project folder

In File Explorer, open the project folder, click the address bar, type
`powershell`, and press **Enter**. A PowerShell window opens in that folder.
Check that it is the correct folder:

```powershell
Get-ChildItem
```

The list should include `README.md` and `backend`.

### Step 4: Create a Python virtual environment

Run this command in the project folder:

```powershell
py -3 -m venv .venv
```

The `.venv` folder keeps this project's Python packages separate from other
Python applications.

### Step 5: Activate the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

When activation succeeds, the PowerShell prompt usually starts with
`(.venv)`. If PowerShell says that script execution is disabled, run the
following command in the same window, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

This changes the policy only for the current PowerShell window.

### Step 6: Install the packages in `requirements.txt`

The project keeps its Python dependency list in
[`backend/requirements.txt`](backend/requirements.txt). Install every listed
package with:

```powershell
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

Wait for pip to finish. Do not run `pip install requirements.txt`; the `-r`
option tells pip to install the packages listed inside the file.

### Step 7: Install Ollama for AI question generation

Ollama runs the language model locally on your computer. Skip this step if
you want to use the built-in template-based question generator instead.

1. Download and install Ollama for Windows from
   [ollama.com/download](https://ollama.com/download).
2. Open a new PowerShell window after installation and download the default
   model:

   ```powershell
   ollama pull qwen3:1.7b
   ```

3. Confirm the model is installed:

   ```powershell
   ollama list
   ```

The Ollama application normally runs in the background. If the `ollama`
command is not recognized, close and reopen PowerShell. The web app uses
`qwen3:1.7b` by default when Ollama is running. If Ollama is not installed
or running, the app can fall back to template-based generation.

### Step 8: (Optional) Set up OCR for scanned PDFs

Skip this step if your PDFs contain selectable text or you will upload DOCX,
PPTX, or TXT files. OCR is only needed to read scanned/image-based PDFs.

1. Install the Tesseract OCR application from the
   [UB Mannheim Tesseract page](https://github.com/UB-Mannheim/tesseract/wiki).
   During installation, make sure to include the language data you need.
2. Download and extract a Windows Poppler release from
   [poppler-windows releases](https://github.com/oschwartz10612/poppler-windows/releases).
   Find the extracted `bin` folder containing `pdftoppm.exe`.
3. Add both program folders to Windows `PATH`:
   1. Open Start and search for **Edit environment variables for your account**.
   2. Select **Path**, then **Edit**, then **New**.
   3. Paste the folder containing `tesseract.exe` and select **New** again.
   4. Paste the Poppler `bin` folder containing `pdftoppm.exe`, then select
      **OK** in each open dialog to save.
4. Close and reopen PowerShell, then verify that Windows can find both
   programs:

   ```powershell
   tesseract --version
   pdftoppm -h
   ```

English, Hindi, and Marathi OCR are supported when their Tesseract language
data is installed.

### Step 9: Start the application

Return to the project-folder PowerShell window from Step 3. Activate the
virtual environment if it is not already active:

```powershell
.\.venv\Scripts\Activate.ps1
```

Start the app:

```powershell
python backend\app.py
```

Leave this window open while using the app. When the server has started,
open **http://127.0.0.1:5000** in your browser. To stop the server, return
to PowerShell and press **Ctrl+C**.

### Step 10: Use the application

1. Upload study material in PDF, DOCX, PPTX, or TXT format.
2. Set up modules, weightages, difficulty, and the marks scheme.
3. Generate and review the questions.
4. Export the question paper or answer key as PDF or DOCX.

The app is a local, single-user application. Uploaded files and generated
work are stored in memory and are cleared when the server restarts or a new
project is created.

### Run it again later

After the first installation, you do not need to reinstall the packages.
Open PowerShell in the project folder and run:

```powershell
.\.venv\Scripts\Activate.ps1
python backend\app.py
```

Then open **http://127.0.0.1:5000** in your browser.

### Optional: use a different port

If port 5000 is already being used by another application, set another port
before starting the server:

```powershell
$env:PORT = "5001"
python backend\app.py
```

Then open **http://127.0.0.1:5001**.

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
- Flask for the local web application and API
- Ollama to run the optional local language model
- `qwen3:1.7b` as the default Ollama model
- Tesseract OCR and Poppler for optional scanned-PDF text extraction
- Python libraries listed in [`backend/requirements.txt`](backend/requirements.txt):
  Flask, pypdf, python-docx, reportlab, nltk, Werkzeug, pytesseract, and pdf2image

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

When Ollama is installed and running, the app uses the locally installed
`qwen3:1.7b` model by default. The model is pretrained; this project does
not train or fine-tune it. Ollama runs the model on the user's computer, and
the Flask backend communicates with it through Ollama's local HTTP API
(default address: `http://127.0.0.1:11434`).

In simple terms, question generation works like this:

1. The app extracts and selects passages from the uploaded study material.
2. The backend sends the passages and instructions to the local model through
   Ollama.
3. The model proposes question-and-answer candidates in a structured format.
4. The app checks the candidates against the source text, removes invalid or
   duplicate questions, and uses supported candidates to build the paper.

The model proposes wording; application code performs the checks and decides
which candidates to keep. This is model inference, not model training.

The project supports:

- model-first generation when Ollama is active,
- template-based fallback when the model is disabled, unavailable, or returns no valid candidates,
- multilingual handling through alternate generation flows for Devanagari-majority text.

Ollama is a model runner, not the language model itself: `qwen3:1.7b` is the
model, and Ollama downloads and runs it locally. Tesseract is separate: it is
OCR software that reads text from scanned page images; it does not generate
questions.

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

- text cleaning and sentence/chunk splitting
- concept and phrase extraction from source passages
- rule-based template question generation as a fallback
- source-evidence and question-structure checks
- duplicate filtering and difficulty/marks heuristics

### Generative component

- pretrained local model: `qwen3:1.7b`
- run by Ollama and called from the backend's local HTTP API
- generates candidate questions from supplied source excerpts; it is not trained by this project
- candidates are validated in the application; template generation is the fallback

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

### Required

- Python 3.10 or newer
- `pip` (included with Python)
- The packages listed in [`backend/requirements.txt`](backend/requirements.txt)

Ollama is optional. Tesseract OCR and Poppler are only needed for scanned or
image-based PDFs; ordinary text-based PDFs do not require them.

The exact Python package names and versions are maintained in
[`backend/requirements.txt`](backend/requirements.txt). Install them by
following Step 6 in the [Windows Installation and Run Guide](#windows-installation-and-run-guide).

## Installation

All installation steps, including the optional Ollama and OCR setup, are in
the [Windows Installation and Run Guide](#windows-installation-and-run-guide).

When the app first starts, it may download NLTK language data if an internet
connection is available. If the download is unavailable, built-in fallback
tokenizers are used.

---

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
Question-Paper-Generator/
|-- README.md
`-- backend/
    |-- app.py
    |-- requirements.txt
    |-- text_extraction.py
    |-- ocr_extraction.py
    |-- nlp_utils.py
    |-- concept_extraction.py
    |-- question_generator.py
    |-- multilingual_pipeline.py
    |-- ollama_question_writer.py
    |-- pipeline.py
    |-- paper_builder.py
    |-- pdf_export.py
    |-- docx_export.py
    |-- templates/
    |-- static/
    `-- test_*.py
```

---

## Conclusion

The NLP Question Paper Generator demonstrates an end-to-end approach for converting educational materials into structured exam papers using local NLP and language-model techniques. The system is designed to make question generation more efficient, source-aware, and marks-oriented, while keeping the workflow local and transparent.

This project is especially relevant in educational environments where teachers need an AI-assisted but controlled method for generating assessment papers without deploying content to external cloud services.

---

## Notes

This repository contains the application code and tests rather than a validated experimental dataset or published research benchmark. It is intended as a practical system for local educational use and further research evaluation.
