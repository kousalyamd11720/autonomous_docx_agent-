"""
main.py
-------
FastAPI entrypoint. POST /agent is the single autonomous-agent endpoint:

  request (natural language)
      -> planner.generate_plan()        [autonomous planning: agent decides
                                          document type + its own TODO list]
      -> executor.execute_plan()        [agent executes each step it planned]
      -> doc_generator.generate_document() [produces the final .docx]
      -> AgentResponse                  [plan, results, and docx path back
                                          to the caller]
"""
import json
import logging
import os

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.models import AgentRequest, AgentResponse
from app.planner import generate_plan
from app.executor import execute_plan, execute_plan_stream
from app.doc_generator import generate_document

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("agent.main")

app = FastAPI(
    title="Autonomous Document Agent",
    description="Accepts a natural-language request, autonomously plans and "
                "executes the steps needed, and returns a generated .docx.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    with open(os.path.join(STATIC_DIR, "index.html"), "r") as f:
        return f.read()


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "autonomous-docx-agent"}


@app.post("/agent/stream")
def run_agent_stream(payload: AgentRequest):
    """
    Same pipeline as POST /agent, but streamed as Server-Sent Events so the
    UI can show the agent's plan and each section being written live instead
    of waiting on one long blocking call.

    Event types sent (each a JSON object on its own "data:" line):
      plan        -> the full autonomous plan, right after planning finishes
      step_start  -> a step is about to be executed
      step_done   -> a step finished (includes used_fallback flag)
      complete    -> docx is ready, includes docx_path + download_url
      error       -> something failed outside the retry/fallback path
    """
    def event_stream():
        def sse(event_type: str, data: dict) -> str:
            return f"data: {json.dumps({'type': event_type, **data})}\n\n"

        try:
            plan = generate_plan(payload.request)
            yield sse("plan", {"plan": plan.model_dump()})

            step_results = []
            for kind, obj in execute_plan_stream(payload.request, plan):
                if kind == "step_start":
                    yield sse("step_start", {"step_id": obj.step_id, "title": obj.title})
                else:  # step_done
                    step_results.append(obj)
                    yield sse("step_done", obj.model_dump())

            docx_path = generate_document(plan, step_results)
            filename = os.path.basename(docx_path)
            used_fallback_llm = any(r.used_fallback for r in step_results)

            yield sse("complete", {
                "docx_path": docx_path,
                "download_url": f"/download/{filename}",
                "used_fallback_llm": used_fallback_llm,
            })
        except Exception as e:
            logger.exception("Streaming agent pipeline failed")
            yield sse("error", {"message": str(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/agent", response_model=AgentResponse)
def run_agent(payload: AgentRequest):
    logger.info("Received request: %s", payload.request)

    try:
        # 1. AUTONOMOUS PLANNING: agent decides document type + its own step list
        plan = generate_plan(payload.request)
        logger.info("Plan generated: %s (%d steps)", plan.document_type, len(plan.steps))

        # 2. EXECUTION: agent runs each step of the plan it created
        step_results = execute_plan(payload.request, plan)

        # 3. OUTPUT: assemble into a polished Word document
        docx_path = generate_document(plan, step_results)

        used_fallback_llm = any(r.used_fallback for r in step_results)

        return AgentResponse(
            status="success",
            document_type=plan.document_type,
            document_title=plan.document_title,
            assumptions=plan.assumptions,
            plan=plan,
            step_results=step_results,
            used_fallback_llm=used_fallback_llm,
            docx_path=docx_path,
            message=(
                f"Generated '{plan.document_title}' ({plan.document_type}) "
                f"with {len(step_results)} sections."
                + (" Note: one or more sections used fallback content because "
                   "the LLM was unavailable." if used_fallback_llm else "")
            ),
        )

    except Exception as e:
        logger.exception("Agent pipeline failed unexpectedly")
        raise HTTPException(status_code=500, detail=f"Agent pipeline failed: {e}")


@app.get("/download/{filename}")
def download_document(filename: str):
    # Basic guardrail: prevent path traversal outside the outputs directory
    safe_name = os.path.basename(filename)
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "outputs", safe_name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=safe_name,
    )
