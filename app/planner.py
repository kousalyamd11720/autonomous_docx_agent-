"""
planner.py
----------
This is the "autonomous planning" part of the agent: given a free-text
request, decide WHAT kind of document is needed and WHAT sections/steps
are required to produce it -- without the caller specifying any of that
structure up front.

Flow:
1. Ask the LLM to act as a planning agent and return a strict JSON plan:
   document_type, document_title, assumptions (for ambiguous asks),
   and an ordered list of steps (each step = one section of the doc).
2. If the LLM is unavailable (see llm_client's retry/fallback), fall back
   to a deterministic generic business-document plan so the request still
   completes end-to-end.
"""
import json
import logging
from typing import List

from app.models import ExecutionPlan, PlanStep
from app.llm_client import call_llm, LLMUnavailableError

logger = logging.getLogger("agent.planner")

PLANNER_SYSTEM_PROMPT = """You are the planning module of an autonomous AI agent.
Given a user's natural language request, you decide:
1. What TYPE of business document best satisfies the request (e.g. Business
   Proposal, Meeting Minutes, Project Plan, Technical Design Doc, SOP,
   Product Specification, Business Report).
2. A short, professional title for that document.
3. If the request is ambiguous, incomplete, or has conflicting requirements,
   list the reasonable assumptions you are making to proceed anyway.
4. A step-by-step execution plan (3 to 7 steps) where EACH step corresponds
   to ONE section of the final document. Order the steps the way the
   sections should appear in the document.

Respond with ONLY valid JSON, no markdown fences, no commentary, in exactly
this schema:
{
  "document_type": "string",
  "document_title": "string",
  "assumptions": ["string", ...],
  "steps": [
    {"step_id": 1, "title": "short step name", "description": "what this step must accomplish", "section_heading": "heading text for this section in the doc"},
    ...
  ]
}
"""


def _fallback_plan(user_request: str) -> ExecutionPlan:
    """
    Deterministic baseline plan used only when the LLM is unavailable after
    retries. Guarantees the pipeline still returns a usable document instead
    of a bare error, at the cost of being generic rather than tailored.
    """
    logger.info("Planner falling back to deterministic generic plan.")
    steps = [
        PlanStep(step_id=1, title="Executive Summary",
                 description=f"Summarize the purpose of this document based on the request: {user_request}",
                 section_heading="Executive Summary"),
        PlanStep(step_id=2, title="Background / Context",
                 description="Provide relevant background context for this request.",
                 section_heading="Background"),
        PlanStep(step_id=3, title="Main Details",
                 description="Lay out the core content addressing the request in detail.",
                 section_heading="Details"),
        PlanStep(step_id=4, title="Next Steps",
                 description="Outline recommended next steps or action items.",
                 section_heading="Next Steps"),
    ]
    return ExecutionPlan(
        document_type="Business Document",
        document_title="Generated Document",
        assumptions=[
            "LLM planning service was unavailable, so a generic four-section "
            "business document structure was used instead of a tailored plan."
        ],
        steps=steps,
    )


def generate_plan(user_request: str) -> ExecutionPlan:
    """Autonomously determine document type + section-by-section execution plan."""
    try:
        raw = call_llm(
            prompt=f"User request:\n{user_request}",
            system_prompt=PLANNER_SYSTEM_PROMPT,
            json_mode=True,
        )
        data = json.loads(raw)
        steps = [PlanStep(**s) for s in data["steps"]]
        return ExecutionPlan(
            document_type=data["document_type"],
            document_title=data["document_title"],
            assumptions=data.get("assumptions", []),
            steps=steps,
        )
    except (LLMUnavailableError, KeyError, ValueError, TypeError) as e:
        logger.warning("Planning via LLM failed (%s); using fallback plan.", e)
        return _fallback_plan(user_request)
