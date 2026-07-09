"""
Pydantic models shared across the agent pipeline.
Keeping these in one place makes the planner -> executor -> doc_generator
contract explicit and easy to validate at each step.
"""
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class AgentRequest(BaseModel):
    """Incoming request body for POST /agent"""
    request: str = Field(..., min_length=3, max_length=4000)

    @field_validator("request")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("request must not be blank")
        return v.strip()


class PlanStep(BaseModel):
    """A single unit of work the agent decided it needs to do."""
    step_id: int
    title: str
    description: str
    section_heading: str  # heading this step will produce in the final docx


class ExecutionPlan(BaseModel):
    """The agent's self-generated TODO list for a given request."""
    document_type: str          # e.g. "Business Proposal", "Meeting Minutes"
    document_title: str
    assumptions: List[str] = []  # assumptions the agent made for ambiguous asks
    steps: List[PlanStep]


class StepResult(BaseModel):
    step_id: int
    section_heading: str
    content: str
    used_fallback: bool = False


class AgentResponse(BaseModel):
    status: str
    document_type: str
    document_title: str
    assumptions: List[str]
    plan: ExecutionPlan
    step_results: List[StepResult]
    used_fallback_llm: bool
    docx_path: str
    message: str
