"""
executor.py
-----------
Executes each step of the plan produced by planner.py. Each step becomes
one section of content for the final document. This is the "action" phase
of the plan -> act loop.

Same retry/fallback contract as the planner: if all LLM providers fail to
produce content for a step after retries, we substitute clean professional
template content rather than failing the whole request.
"""
import logging
from datetime import datetime
from typing import List

from app.models import PlanStep, StepResult, ExecutionPlan
from app.llm_client import call_llm, LLMUnavailableError

logger = logging.getLogger("agent.executor")

EXECUTOR_SYSTEM_PROMPT_TEMPLATE = """You are the execution module of an autonomous AI agent.
Today's real-world date is {today}. Any date you mention or assume (launch
dates, milestones, deadlines) must be a realistic future date relative to
{today} -- never use a date from your training data by default.

You are given the overall document type/title, the full list of OTHER
section headings in this document (for context only), and ONE specific
step to execute. Write the content for that section only.

Rules:
- Write in a professional, polished business-document tone.
- 100-250 words for this section, using paragraphs and, where useful, bullet
  points written as plain lines (do not use markdown symbols like '#' or '**').
- Be specific and concrete: use real-sounding mock names, figures, and dates
  rather than vague filler like "various stakeholders" or "in due course."
- Do NOT repeat information, phrasing, or talking points that belong in the
  OTHER section headings listed below -- each section must add NEW
  information, not restate the same ideas in different words.
- Do not repeat the section heading in the body text.
- If this section is specifically about Budget, Cost, or Timeline/Schedule,
  present the core figures as a markdown table (header row + rows separated
  by "|", e.g. "| Phase | Duration | Cost |") instead of prose paragraphs,
  optionally with 1-2 sentences of framing before or after the table.
- Return ONLY the section body text, nothing else.
"""


def _executor_system_prompt() -> str:
    today = datetime.now().strftime("%B %d, %Y")
    return EXECUTOR_SYSTEM_PROMPT_TEMPLATE.format(today=today)


def _fallback_content(step: PlanStep, user_request: str, document_type: str) -> str:
    """Generate professional request-aware content without exposing failures."""
    request = " ".join(user_request.strip().split())
    heading = f"{step.title} {step.section_heading}".lower()
    context = f"This {document_type.lower()} addresses this objective: {request}. "

    if any(word in heading for word in ("summary", "overview")):
        return context + (
            "The recommended approach is to define the intended outcome clearly, "
            "confirm the people and resources involved, and organize delivery into "
            "measurable stages. Success should be evaluated against agreed scope, "
            "quality expectations, timing, and practical value. This gives decision-makers "
            "a concise basis for alignment while leaving room to refine details as input "
            "from the responsible stakeholders becomes available."
        )
    if any(word in heading for word in ("background", "objective", "purpose", "scope")):
        return context + (
            "The work should remain focused on the stated need and the audience that will "
            "use the result. The initial scope includes clarifying requirements, identifying "
            "dependencies, documenting constraints, and agreeing how the finished work will "
            "be reviewed. Items not supported by the original request should be treated as "
            "open questions rather than assumed commitments."
        )
    if any(word in heading for word in ("timeline", "milestone", "implementation", "procedure")):
        return context + (
            "Delivery can follow a controlled four-stage approach:\n"
            "| Stage | Primary activity | Completion signal |\n"
            "| --- | --- | --- |\n"
            "| Initiation | Confirm scope, owner, and requirements | Scope approved |\n"
            "| Preparation | Develop materials and resources | Draft ready for review |\n"
            "| Delivery | Execute the agreed work and track issues | Deliverables completed |\n"
            "| Closeout | Validate outcomes and record follow-up actions | Acceptance recorded |\n"
            "Dates and named owners should be confirmed before execution begins."
        )
    if any(word in heading for word in ("risk", "quality", "control", "acceptance")):
        return context + (
            "Quality should be checked through documented requirements, peer review, and "
            "final approval by the accountable owner. Key risks include unclear scope, "
            "missing inputs, unrealistic timing, and gaps in ownership. These can be reduced "
            "with a decision log, named action owners, agreed review checkpoints, and early "
            "escalation of unresolved blockers. Acceptance should use observable outcomes."
        )
    if any(word in heading for word in ("decision", "action", "recommend", "next step")):
        return context + (
            "The immediate priority is to validate scope with the document owner and identify "
            "the stakeholders needed for approval or delivery. Next, assign an owner to each "
            "work item, agree realistic target dates, and record outstanding questions. "
            "Progress should be reviewed regularly, with decisions and changes documented. "
            "Final approval should confirm the requested outcome and ownership of remaining actions."
        )
    if any(word in heading for word in ("role", "responsibil")):
        return context + (
            "A document owner should maintain scope, coordinate inputs, and obtain approval. "
            "Contributors provide accurate and timely subject-matter input, while the reviewer "
            "checks completeness, consistency, and usability. The approving stakeholder confirms "
            "that the result meets the intended need. Every action should have one accountable "
            "owner, a target date, and a clear completion condition."
        )
    return context + (
        f"The focus of this section is to {step.description.rstrip('.')}. "
        "The proposed approach is to confirm requirements, organize the work into clear "
        "deliverables, and assign ownership before execution. Constraints and dependencies "
        "should be recorded explicitly, and important decisions validated with appropriate "
        "stakeholders. The completed output should be reviewed for accuracy, usefulness, "
        "and alignment with the original objective."
    )


def execute_plan_stream(user_request: str, plan: ExecutionPlan):
    """
    Generator version of execute_plan for the streaming UI: yields
    ('step_start', step) right before a step runs and
    ('step_done', StepResult) right after, so the frontend can render
    progress live instead of waiting for the whole plan to finish.
    """
    plan_context = "; ".join(f"Step {s.step_id}: {s.title}" for s in plan.steps)
    system_prompt = _executor_system_prompt()

    for step in plan.steps:
        yield ("step_start", step)

        other_headings = [s.section_heading for s in plan.steps if s.step_id != step.step_id]
        prompt = (
            f"Original user request: {user_request}\n"
            f"Document type: {plan.document_type}\n"
            f"Document title: {plan.document_title}\n"
            f"Full plan (context only): {plan_context}\n"
            f"OTHER section headings in this document (do not repeat their content): {', '.join(other_headings) or 'none'}\n\n"
            f"Now write the content for this specific step:\n"
            f"Step title: {step.title}\n"
            f"Step goal: {step.description}\n"
            f"Section heading: {step.section_heading}"
        )
        try:
            content = call_llm(prompt=prompt, system_prompt=system_prompt)
            result = StepResult(
                step_id=step.step_id,
                section_heading=step.section_heading,
                content=content.strip(),
                used_fallback=False,
            )
        except LLMUnavailableError as e:
            logger.warning(
                "Execution of step %d failed (%s); using fallback content.",
                step.step_id, e,
            )
            result = StepResult(
                step_id=step.step_id,
                section_heading=step.section_heading,
                content=_fallback_content(step, user_request, plan.document_type),
                used_fallback=True,
            )
        yield ("step_done", result)


def execute_plan(user_request: str, plan: ExecutionPlan) -> List[StepResult]:
    results: List[StepResult] = []
    plan_context = "; ".join(f"Step {s.step_id}: {s.title}" for s in plan.steps)
    system_prompt = _executor_system_prompt()

    for step in plan.steps:
        other_headings = [s.section_heading for s in plan.steps if s.step_id != step.step_id]
        prompt = (
            f"Original user request: {user_request}\n"
            f"Document type: {plan.document_type}\n"
            f"Document title: {plan.document_title}\n"
            f"Full plan (context only): {plan_context}\n"
            f"OTHER section headings in this document (do not repeat their content): {', '.join(other_headings) or 'none'}\n\n"
            f"Now write the content for this specific step:\n"
            f"Step title: {step.title}\n"
            f"Step goal: {step.description}\n"
            f"Section heading: {step.section_heading}"
        )
        try:
            content = call_llm(prompt=prompt, system_prompt=system_prompt)
            results.append(StepResult(
                step_id=step.step_id,
                section_heading=step.section_heading,
                content=content.strip(),
                used_fallback=False,
            ))
        except LLMUnavailableError as e:
            logger.warning("Execution of step %d failed (%s); using fallback content.",
                            step.step_id, e)
            results.append(StepResult(
                step_id=step.step_id,
                section_heading=step.section_heading,
                content=_fallback_content(step, user_request, plan.document_type),
                used_fallback=True,
            ))

    return results
