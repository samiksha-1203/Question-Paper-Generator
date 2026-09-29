"""
pdf_export.py
-------------
Renders the new question-paper JSON structure into print-ready PDFs using
ReportLab: a professional exam paper (letterhead, instructions, sectioned
questions with marks) and a separate answer-key PDF (question, model
answer, marks, difficulty, Bloom's level, module).
"""

import re
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, ListFlowable, ListItem
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from paper_builder import SECTION_GROUPS, section_for_question, iter_logical_units

# ReportLab's built-in base-14 fonts (Helvetica etc.) have no Devanagari
# glyphs at all - text in Hindi/Marathi would render as solid black boxes.
# Lohit Devanagari covers both Devanagari AND Latin, so registering it once
# and using it for the whole document whenever any Devanagari text is
# present renders both scripts correctly.
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_DEVANAGARI_FONT_PATH = "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf"
_devanagari_font_registered = False


def _ensure_devanagari_font():
    global _devanagari_font_registered
    if _devanagari_font_registered:
        return True
    try:
        pdfmetrics.registerFont(TTFont("Devanagari", _DEVANAGARI_FONT_PATH))
        _devanagari_font_registered = True
    except Exception:
        _devanagari_font_registered = False
    return _devanagari_font_registered


def _paper_has_devanagari(paper: dict) -> bool:
    meta = paper.get("meta", {})
    texts = [meta.get("title", ""), meta.get("subject", "")]
    texts += meta.get("instructions", [])
    for q in paper.get("questions", []):
        texts.append(q.get("question", ""))
        texts.append(q.get("answer", ""))
    return any(_DEVANAGARI_RE.search(t or "") for t in texts)


def _styles(use_devanagari=False):
    ss = getSampleStyleSheet()
    body_font = "Helvetica"
    bold_font = "Helvetica-Bold"
    if use_devanagari and _ensure_devanagari_font():
        body_font = "Devanagari"
        bold_font = "Devanagari"  # no separate bold variant installed; same face is used

    ss.add(ParagraphStyle(name="PaperTitle", fontSize=18, leading=22, alignment=TA_CENTER,
                           fontName=bold_font, spaceAfter=2))
    ss.add(ParagraphStyle(name="PaperSub", fontSize=10.5, leading=14, alignment=TA_CENTER,
                           fontName=body_font, textColor=colors.HexColor("#4a4a4a")))
    ss.add(ParagraphStyle(name="SectionHead", fontSize=12.5, leading=16, fontName=bold_font,
                           textColor=colors.HexColor("#1B2A4A"), spaceBefore=14, spaceAfter=6))
    ss.add(ParagraphStyle(name="QText", fontSize=10.5, leading=15, alignment=TA_LEFT,
                           fontName=body_font, spaceAfter=4))
    ss.add(ParagraphStyle(name="Marks", fontSize=9, leading=12, fontName=body_font,
                           textColor=colors.HexColor("#666666")))
    ss.add(ParagraphStyle(name="Instr", fontSize=9.5, leading=13, fontName=body_font))
    ss.add(ParagraphStyle(name="AnswerCell", fontSize=8.5, leading=11, fontName=body_font))
    return ss


def build_paper_pdf(paper: dict) -> BytesIO:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                             leftMargin=18 * mm, rightMargin=18 * mm)
    styles = _styles(use_devanagari=_paper_has_devanagari(paper))
    story = []
    meta = paper["meta"]
    questions = paper["questions"]

    story.append(Paragraph(meta["title"], styles["PaperTitle"]))
    story.append(Paragraph(
        f"Subject: {meta['subject']} | Paper for: {meta.get('education_level', 'General / Mixed')}",
        styles["PaperSub"],
    ))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1B2A4A")))
    story.append(Spacer(1, 8))

    total_marks = meta.get("total_marks_actual", meta.get("total_marks_target", ""))
    info_table = Table(
        [["Duration:", meta["duration"], "Total Marks:", str(total_marks)],
         ["Name:", "_" * 30, "Roll No.:", "_" * 18]],
        colWidths=[65, 170, 75, 120],
    )
    info_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6))

    instructions = meta.get("instructions") or []
    if instructions:
        story.append(Paragraph("<b>Instructions:</b>", styles["Instr"]))
        items = [ListItem(Paragraph(i, styles["Instr"])) for i in instructions]
        story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=14))
        story.append(Spacer(1, 4))

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#999999")))
    story.append(Spacer(1, 10))

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
        story.append(Paragraph(f"Section {label} — {name}", styles["SectionHead"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
        story.append(Spacer(1, 4))

        for kind, item in units:
            if kind == "single":
                q = item
                story.append(Paragraph(f"{q['number']}. {q['question']}", styles["QText"]))
                story.append(Paragraph(f"[{q['marks']} marks]", styles["Marks"]))
                story.append(Spacer(1, 8))
            else:
                siblings = item
                attempt = min(siblings[0].get("group_attempt", len(siblings)), len(siblings))
                marks_val = siblings[0]["marks"]
                story.append(Paragraph(
                    f"{siblings[0]['number']}. Attempt any {attempt} of the following "
                    f"{len(siblings)} ({marks_val} marks each):",
                    styles["QText"],
                ))
                for sib in siblings:
                    label = sib.get("group_label", "")
                    story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;{label} {sib['question']}", styles["QText"]))
                story.append(Spacer(1, 8))

        story.append(Spacer(1, 6))

    doc.build(story)
    buf.seek(0)
    return buf


def build_answer_key_pdf(paper: dict) -> BytesIO:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                             leftMargin=14 * mm, rightMargin=14 * mm)
    styles = _styles(use_devanagari=_paper_has_devanagari(paper))
    story = []
    meta = paper["meta"]
    questions = paper["questions"]

    story.append(Paragraph(f"{meta['title']} — Answer Key", styles["PaperTitle"]))
    story.append(Paragraph(
        f"Subject: {meta['subject']} | Paper for: {meta.get('education_level', 'General / Mixed')}",
        styles["PaperSub"],
    ))
    story.append(Spacer(1, 10))

    data = [["Q#", "Module", "Type", "Question / Model Answer", "Marks", "Diff. / Bloom"]]
    for kind, item in iter_logical_units(questions):
        if kind == "single":
            q = item
            qa_para = Paragraph(
                f"<b>Q:</b> {q['question']}<br/><b>A:</b> {q['answer']}", styles["AnswerCell"]
            )
            data.append([
                str(q["number"]), q.get("module", ""), q.get("type", ""), qa_para,
                str(q["marks"]), f'{q.get("difficulty","")}\n{q.get("bloom","")}',
            ])
        else:
            siblings = item
            attempt = min(siblings[0].get("group_attempt", len(siblings)), len(siblings))
            header = Paragraph(
                f"<b>Attempt any {attempt} of the following {len(siblings)}:</b>", styles["AnswerCell"]
            )
            data.append([str(siblings[0]["number"]), siblings[0].get("module", ""), "", header, "", ""])
            for sib in siblings:
                qa_para = Paragraph(
                    f"<b>{sib.get('group_label','')} Q:</b> {sib['question']}<br/><b>A:</b> {sib['answer']}",
                    styles["AnswerCell"],
                )
                data.append([
                    "", sib.get("module", ""), sib.get("type", ""), qa_para,
                    str(sib["marks"]), f'{sib.get("difficulty","")}\n{sib.get("bloom","")}',
                ])

    t = Table(data, colWidths=[25, 60, 60, 250, 35, 60], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B2A4A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f3ec")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)

    doc.build(story)
    buf.seek(0)
    return buf
