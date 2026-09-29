"""
docx_export.py
---------------
Builds real, editable Microsoft Word (.docx) documents for the question
paper and for the answer key, using python-docx.
"""

from io import BytesIO
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

NAVY = RGBColor(0x1B, 0x2A, 0x4A)


def _set_cell_shading(cell, hex_color):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def build_paper_docx(paper: dict) -> BytesIO:
    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(1.8)
        section.bottom_margin = Cm(1.6)
        section.left_margin = Cm(1.8)
        section.right_margin = Cm(1.8)

    meta = paper["meta"]
    questions = paper["questions"]

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(meta["title"])
    run.bold = True
    run.font.size = Pt(18)
    run.font.color.rgb = NAVY

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = sub.add_run(
        f"Subject: {meta['subject']} | Paper for: {meta.get('education_level', 'General / Mixed')}"
    )
    sub_run.font.size = Pt(11)

    info = doc.add_table(rows=2, cols=4)
    info.alignment = WD_TABLE_ALIGNMENT.CENTER
    cells = info.rows[0].cells
    cells[0].text = "Duration:"
    cells[1].text = str(meta["duration"])
    cells[2].text = "Total Marks:"
    cells[3].text = str(meta.get("total_marks_actual", meta.get("total_marks_target", "")))
    cells2 = info.rows[1].cells
    cells2[0].text = "Name:"
    cells2[1].text = "_" * 28
    cells2[2].text = "Roll No.:"
    cells2[3].text = "_" * 16
    for row in info.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(10)

    doc.add_paragraph()
    instr_head = doc.add_paragraph()
    instr_head.add_run("Instructions:").bold = True
    for instr in meta.get("instructions", []):
        doc.add_paragraph(instr, style="List Bullet")

    doc.add_paragraph()

    from paper_builder import SECTION_GROUPS, section_for_question, iter_logical_units
    sections = {}
    seen_groups = set()
    for q in questions:
        gid = q.get("group_id")
        if gid:
            if gid in seen_groups:
                continue
            seen_groups.add(gid)
            item = [sibling for sibling in questions if sibling.get("group_id") == gid]
            representative = item[0]
            kind = "group"
        else:
            item = q
            representative = q
            kind = "single"
        label, name = section_for_question(representative)
        sections.setdefault((label, name), []).append((kind, item))

    for label, name, _ in SECTION_GROUPS:
        units = sections.get((label, name))
        if not units:
            continue
        head = doc.add_paragraph()
        head_run = head.add_run(f"Section {label} — {name}")
        head_run.bold = True
        head_run.font.size = Pt(13)
        head_run.font.color.rgb = NAVY

        for kind, item in units:
            if kind == "single":
                q = item
                p = doc.add_paragraph()
                p.add_run(f"{q['number']}. ").bold = True
                p.add_run(f"{q['question']}  ")
                marks_run = p.add_run(f"[{q['marks']} marks]")
                marks_run.font.size = Pt(9)
                marks_run.italic = True
                doc.add_paragraph()  # answer space
            else:
                siblings = item
                attempt = min(siblings[0].get("group_attempt", len(siblings)), len(siblings))
                marks_val = siblings[0]["marks"]
                p = doc.add_paragraph()
                p.add_run(f"{siblings[0]['number']}. ").bold = True
                p.add_run(f"Attempt any {attempt} of the following {len(siblings)} ({marks_val} marks each):")
                for sib in siblings:
                    sub = doc.add_paragraph()
                    sub.paragraph_format.left_indent = Cm(0.6)
                    sub.add_run(f"{sib.get('group_label','')} ").bold = True
                    sub.add_run(sib["question"])
                doc.add_paragraph()  # answer space

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def build_answer_key_docx(paper: dict) -> BytesIO:
    doc = Document()
    meta = paper["meta"]
    questions = paper["questions"]

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(f"{meta['title']} — Answer Key")
    run.bold = True
    run.font.size = Pt(16)
    run.font.color.rgb = NAVY
    doc.add_paragraph()

    table = doc.add_table(rows=1, cols=7)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    headers = ["Q#", "Module", "Type", "Question", "Model Answer", "Marks", "Difficulty / Bloom"]
    for i, h in enumerate(headers):
        hdr[i].text = h
        for p in hdr[i].paragraphs:
            for r in p.runs:
                r.bold = True

    for q in questions:
        row = table.add_row().cells
        label_prefix = f"{q.get('group_label','')} " if q.get("group_id") else ""
        row[0].text = str(q["number"])
        row[1].text = str(q.get("module", ""))
        row[2].text = q.get("type", "")
        row[3].text = f"{label_prefix}{q.get('question', '')}"
        row[4].text = q.get("answer", "")
        row[5].text = str(q.get("marks", ""))
        row[6].text = f'{q.get("difficulty", "")} / {q.get("bloom", "")}'

    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
