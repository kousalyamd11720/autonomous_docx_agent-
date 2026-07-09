"""
doc_generator.py
-----------------
Assembles the agent's plan + step results into a polished .docx file
using python-docx.
"""
import os
import re
import uuid
from datetime import datetime
from typing import List

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.models import ExecutionPlan, StepResult

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "outputs")
ACCENT_COLOR = RGBColor(0x1F, 0x4E, 0x79)


def _add_title_page(doc: Document, plan: ExecutionPlan):
    title = doc.add_heading(plan.document_title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.color.rgb = ACCENT_COLOR

    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(plan.document_type)
    run.italic = True
    run.font.size = Pt(13)

    date_p = doc.add_paragraph()
    date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_run = date_p.add_run(datetime.now().strftime("%B %d, %Y"))
    date_run.font.size = Pt(10)
    date_run.font.color.rgb = RGBColor(0x60, 0x60, 0x60)

    doc.add_paragraph()


def _add_assumptions(doc: Document, assumptions: List[str]):
    if not assumptions:
        return
    heading = doc.add_heading("Assumptions Made By The Agent", level=1)
    for run in heading.runs:
        run.font.color.rgb = ACCENT_COLOR
    for a in assumptions:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(a)
    doc.add_paragraph()


def _add_section(doc: Document, heading_text: str, content: str, used_fallback: bool):
    heading = doc.add_heading(heading_text, level=1)
    for run in heading.runs:
        run.font.color.rgb = ACCENT_COLOR

    # Split content into paragraphs/bullets in a lightweight, dependency-free way.
    lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
    for line in lines:
        bullet_match = re.match(r"^[-*•]\s+(.*)", line)
        if bullet_match:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(bullet_match.group(1))
        else:
            p = doc.add_paragraph(line)
            p.paragraph_format.space_after = Pt(6)

    if used_fallback:
        note = doc.add_paragraph()
        note_run = note.add_run("(Note: generated via fallback logic — LLM was unavailable for this section.)")
        note_run.italic = True
        note_run.font.size = Pt(9)
        note_run.font.color.rgb = RGBColor(0xA0, 0x30, 0x30)

    doc.add_paragraph()


def generate_document(plan: ExecutionPlan, step_results: List[StepResult]) -> str:
    """Builds the docx file and returns its filesystem path."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    doc = Document()

    # Base style tweaks
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    _add_title_page(doc, plan)
    _add_assumptions(doc, plan.assumptions)

    results_by_id = {r.step_id: r for r in step_results}
    for step in plan.steps:
        result = results_by_id.get(step.step_id)
        if result is None:
            continue
        _add_section(doc, result.section_heading, result.content, result.used_fallback)

    filename = f"{plan.document_type.replace(' ', '_')}_{uuid.uuid4().hex[:8]}.docx"
    path = os.path.join(OUTPUT_DIR, filename)
    doc.save(path)
    return path
