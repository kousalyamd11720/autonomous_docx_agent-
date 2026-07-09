# Autonomous Document Agent

A minimal autonomous AI agent that takes a natural-language request, **plans
its own steps**, **executes** them, and produces a polished **.docx**.

## Architecture

```
POST /agent {"request": "..."}
        │
        ▼
  planner.py        <- LLM (Groq / llama-3.3-70b-versatile) decides:
                         - document_type, document_title
                         - assumptions (for ambiguous requests)
                         - an ordered step list (its own TODO list),
                           one step per document section
        │
        ▼
  executor.py        <- for each planned step, calls the LLM to write
                         that section's content (mock data allowed)
        │
        ▼
  doc_generator.py    <- assembles plan + section content into a
                         formatted .docx (python-docx)
        │
        ▼
  AgentResponse        <- plan, per-step results, docx path, status
```

Each stage is a separate module so planning, execution, and rendering can be
tested/replaced independently.

### Engineering improvement: Retry & Fallback logic (`llm_client.py`)

Every LLM call (planning AND section-writing) goes through `call_llm()`,
which:
1. Retries transient failures (timeouts, connection errors, rate limits, or
   the model returning invalid JSON when JSON was required) up to 3 times
   with increasing backoff.
2. If all retries fail, raises `LLMUnavailableError` instead of crashing the
   request. The **planner** catches this and substitutes a deterministic
   4-section generic business-document plan; the **executor** catches this
   per-step and substitutes clearly-labeled placeholder content.
3. The API response includes `used_fallback_llm` so the caller always knows
   whether real LLM output or fallback content was used.

**Why this one, over the other options:** planning and generation both sit
behind one external dependency (the LLM). Without retry/fallback, any
transient hiccup returns a 500 and no document at all — which defeats the
purpose of an "autonomous" agent that's supposed to complete the task
regardless. This turns the LLM into a best-effort enhancement over a
deterministic baseline rather than a single point of failure, which is the
kind of thing that matters once this moves past a demo.

## Setup

```bash
cd autonomous_docx_agent
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste a free Groq key from https://console.groq.com/keys
export GROQ_API_KEY=your_key_here      # or `source .env` / use python-dotenv
uvicorn app.main:app --reload
```

Server runs at `http://127.0.0.1:8000`. **Open that URL in a browser** — it
serves the Agent Console UI directly (not just JSON). Interactive API docs
are still available at `http://127.0.0.1:8000/docs`.

### Using the UI

1. Type a request (or click one of the two example chips) and hit **Run agent**.
2. The right panel streams the agent's plan live — each step ticks from
   queued → running → done as the LLM writes that section, over Server-Sent
   Events (`POST /agent/stream`), not a fake loading spinner.
3. The generated document renders directly in the page as each section
   finishes, styled like an actual document.
4. Once complete, a **Download .docx** button appears with the real file.

This sits alongside, not instead of, the plain JSON API: `POST /agent` still
returns the full response in one call (used by `run_demo.py` and curl),
while `POST /agent/stream` powers the live UI.

## Running the two required test cases

With the server running, in another terminal:

```bash
python run_demo.py
```

This fires both test inputs and prints the agent's self-generated plan for
each, plus the path to the generated `.docx` — this is what to show on
screen for the demo.

- **Standard case**: a clear, single-purpose kickoff proposal request.
- **Complex case**: an ambiguous, multi-stakeholder request with no timeline
  given — the agent has to decide the document type itself and state its
  assumptions (see the `assumptions` field in the response and the
  "Assumptions Made By The Agent" section in the generated docx).

You can also hit it directly:

```bash
curl -X POST http://127.0.0.1:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"request": "Write a project kickoff proposal for a mobile banking app"}'
```

Generated files land in `outputs/`. Download via
`GET /download/{filename}`.

## Video script notes (talking points, not a transcript)

**Live Demo (3-4 min)**
- Show `/docs` Swagger UI or `run_demo.py`.
- Fire the standard request → point at the agent-generated `plan.steps` in
  the JSON response, then open the resulting `.docx`.
- Fire the complex/ambiguous request → point specifically at the
  `assumptions` list and the "Assumptions Made By The Agent" section in the
  doc — this is the evidence of autonomous decision-making under ambiguity.

**What You Built (2-3 min)**
- Three-stage pipeline: plan → execute → render, each its own module.
- Planner and executor both use the LLM but with strict JSON-schema prompts
  validated via Pydantic before being trusted.
- `python-docx` builds a real formatted document (headings, bullets,
  assumptions callout) rather than dumping raw text.
- FastAPI for the API layer: automatic request validation via Pydantic,
  `/docs` for free, clean separation of concerns.

**Debugging Insight (1-2 min)** — fill this in with what you actually hit,
e.g.: the model sometimes returned JSON wrapped in prose or markdown fences
when `json_mode` wasn't forced correctly, which broke `json.loads()` in the
planner — root cause was not setting `response_format` on every call
consistently; fixed by centralizing that in `llm_client.call_llm()` so
every JSON-mode call is forced through the same path instead of being
handled ad hoc per caller.

**Tradeoff Discussion (1-2 min)** — recommended: **Autonomous Planning vs
Deterministic Workflows**. This agent lets the LLM choose document type and
section structure per request (flexible, handles the ambiguous test case
well) instead of a fixed template per document type (more predictable,
easier to test, but brittle against novel requests). The fallback plan is
actually the deterministic option, shown side-by-side with the LLM-driven
one — a natural way to make this tradeoff visible on screen.

## Folder structure

```
autonomous_docx_agent/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI app: /, /agent, /agent/stream, /download
│   ├── models.py          # Pydantic schemas (request/plan/steps/response)
│   ├── planner.py         # autonomous planning (+ deterministic fallback)
│   ├── executor.py        # runs each planned step, streaming or batch (+ fallback content)
│   ├── llm_client.py      # Groq wrapper: retry + fallback (the improvement)
│   └── doc_generator.py   # python-docx rendering
├── static/                 # the browser UI
│   ├── index.html
│   ├── style.css
│   └── app.js              # SSE client driving the live console + doc preview
├── outputs/                # generated .docx files land here
├── run_demo.py             # fires the 2 required test cases for the video
├── requirements.txt
├── .env.example
└── README.md
```
