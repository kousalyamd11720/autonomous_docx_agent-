"""
executor.py
-----------
Executes each step of the plan produced by planner.py. Each step becomes
one section of content for the final document. This is the "action" phase
of the plan -> act loop.

Same retry/fallback contract as the planner: if the LLM cannot produce
content for a step after retries, we substitute clearly-labeled placeholder
content (using mock data, as explicitly permitted by the assignment) rather
than failing the whole request.
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


def _fallback_content(step: PlanStep) -> str:
    """Deterministic placeholder content used only if the LLM call fails."""
    return (
        f"[Auto-generated placeholder for '{step.title}'] "
        f"This section was intended to cover: {step.description}. "
        f"Content generation via the LLM was temporarily unavailable, so this "
        f"placeholder was inserted by the agent's fallback logic to ensure the "
        f"document could still be produced end-to-end."
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
                content=_fallback_content(step),
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
                content=_fallback_content(step),
                used_fallback=True,
            ))

    return results
