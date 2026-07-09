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
import re
from datetime import datetime

from app.models import ExecutionPlan, PlanStep
from app.llm_client import call_llm, LLMUnavailableError

logger = logging.getLogger("agent.planner")

PLANNER_SYSTEM_PROMPT_TEMPLATE = """You are the planning module of an autonomous AI agent.
Today's real-world date is {today}. If the request or your plan implies any
dates (launch dates, milestones, deadlines), they must be realistic future
dates relative to {today} -- never reuse a date from your training data.

Given a user's natural language request, you decide:
1. What TYPE of business document best satisfies the request (e.g. Business
   Proposal, Meeting Minutes, Project Plan, Technical Design Doc, SOP,
   Product Specification, Business Report).
2. A short, professional title for that document.
3. If the request is ambiguous, incomplete, or has conflicting requirements,
   list the reasonable assumptions you are making to proceed anyway.
4. A step-by-step execution plan (3 to 7 steps) where EACH step corresponds
   to ONE section of the final document. Order the steps the way the
   sections should appear in the document. If the document type calls for
   budget or timeline information, include a dedicated step for it (e.g.
   "Timeline & Budget") so it can be rendered as a table.

Each step must cover a DISTINCT angle of the request -- do not create two
steps that would end up saying the same thing in different words.

Respond with ONLY valid JSON, no markdown fences, no commentary, in exactly
this schema:
{{
  "document_type": "string",
  "document_title": "string",
  "assumptions": ["string", ...],
  "steps": [
    {{"step_id": 1, "title": "short step name", "description": "what this step must accomplish", "section_heading": "heading text for this section in the doc"}},
    ...
  ]
}}
"""


def _planner_system_prompt() -> str:
    today = datetime.now().strftime("%B %d, %Y")
    return PLANNER_SYSTEM_PROMPT_TEMPLATE.format(today=today)


FALLBACK_TEMPLATES = [
    (("meeting minutes", "minutes of meeting", "mom"), "Meeting Minutes",
     ("Meeting Overview", "Discussion Summary", "Decisions", "Action Items")),
    (("standard operating procedure", "sop", "procedure", "process guide"),
     "Standard Operating Procedure",
     ("Purpose and Scope", "Roles and Responsibilities", "Procedure", "Quality Checks")),
    (("technical design", "architecture", "system design"), "Technical Design Document",
     ("Overview and Objectives", "Proposed Architecture", "Implementation Approach", "Risks and Controls")),
    (("product specification", "product spec", "requirements document", "prd"),
     "Product Specification",
     ("Product Overview", "User Requirements", "Functional Requirements", "Acceptance Criteria")),
    (("project plan", "implementation plan", "rollout plan"), "Project Plan",
     ("Project Overview", "Scope and Deliverables", "Timeline and Milestones", "Risks and Next Steps")),
    (("proposal", "business case", "pitch"), "Business Proposal",
     ("Executive Summary", "Proposed Solution", "Implementation and Timeline", "Value and Next Steps")),
    (("report", "analysis", "assessment", "review"), "Business Report",
     ("Executive Summary", "Background and Objectives", "Analysis and Findings", "Recommendations")),
]


def _fallback_document_type(user_request: str):
    request_lower = user_request.lower()
    for keywords, document_type, headings in FALLBACK_TEMPLATES:
        if any(
            re.search(rf"\b{re.escape(keyword)}\b", request_lower)
            for keyword in keywords
        ):
            return document_type, headings
    return (
        "Business Document",
        ("Executive Summary", "Background and Objectives",
         "Key Considerations", "Recommendations and Next Steps"),
    )


def _fallback_title(user_request: str, document_type: str) -> str:
    subject = re.sub(
        r"^(please\s+)?(create|write|prepare|generate|draft|make)\s+(an?\s+)?",
        "", user_request.strip(), flags=re.IGNORECASE,
    )
    subject = re.sub(r"\s+", " ", subject).strip(" .:-")
    if not subject:
        return document_type
    if len(subject) > 72:
        subject = subject[:69].rsplit(" ", 1)[0] + "..."
    return subject[0].upper() + subject[1:]


def _fallback_plan(user_request: str) -> ExecutionPlan:
    """
    Deterministic baseline plan used only when the LLM is unavailable after
    retries. Guarantees the pipeline still returns a usable document instead
    of a bare error, at the cost of being generic rather than tailored.
    """
    logger.info("Planner using a request-aware deterministic template.")
    document_type, headings = _fallback_document_type(user_request)
    steps = [
        PlanStep(
            step_id=step_id,
            title=heading,
            description=(
                f"Develop the {heading.lower()} for this {document_type.lower()}, "
                f"grounded in the original request: {user_request}"
            ),
            section_heading=heading,
        )
        for step_id, heading in enumerate(headings, start=1)
    ]
    return ExecutionPlan(
        document_type=document_type,
        document_title=_fallback_title(user_request, document_type),
        assumptions=[],
        steps=steps,
    )


def generate_plan(user_request: str) -> ExecutionPlan:
    """Autonomously determine document type + section-by-section execution plan."""
    try:
        raw = call_llm(
            prompt=f"User request:\n{user_request}",
            system_prompt=_planner_system_prompt(),
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
