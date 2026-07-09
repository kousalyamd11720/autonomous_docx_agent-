"""
run_demo.py
-----------
Run this while `uvicorn app.main:app --reload` is running in another
terminal. It fires the two required test inputs (standard + complex/
ambiguous) at POST /agent and prints the agent's self-generated plan plus
the path to each generated .docx -- exactly what you need to show on
screen for the "Live Demo" part of the video.

Usage:
    python run_demo.py
"""
import json
import requests

BASE_URL = "http://127.0.0.1:8000"

TEST_CASES = {
    "STANDARD (clear, single-purpose request)": (
        "Write a project kickoff proposal for a new mobile banking app "
        "for a mid-size regional bank. Include scope, timeline, and budget."
    ),
    "COMPLEX (ambiguous / multi-stakeholder / missing info)": (
        "We need something for the client meeting tomorrow about the "
        "delayed vendor integration -- marketing wants a positive spin, "
        "engineering wants to flag the real risks, and leadership just "
        "wants a one-pager they can forward. Figure out what to make and "
        "just produce it, we don't have the full timeline yet."
    ),
}


def run_case(label: str, request_text: str):
    print("\n" + "=" * 90)
    print(f"TEST CASE: {label}")
    print(f"REQUEST: {request_text}")
    print("=" * 90)

    resp = requests.post(f"{BASE_URL}/agent", json={"request": request_text}, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    print(f"\n>> Agent decided document type : {data['document_type']}")
    print(f">> Agent decided document title: {data['document_title']}")
    if data["assumptions"]:
        print(">> Assumptions the agent made:")
        for a in data["assumptions"]:
            print(f"   - {a}")

    print("\n>> Agent-generated execution plan (its own TODO list):")
    for step in data["plan"]["steps"]:
        print(f"   Step {step['step_id']}: {step['title']} -> section '{step['section_heading']}'")

    print(f"\n>> Used fallback logic (LLM unavailable at any point)? {data['used_fallback_llm']}")
    print(f">> Generated document saved at: {data['docx_path']}")
    print(f">> Message: {data['message']}")


if __name__ == "__main__":
    for label, text in TEST_CASES.items():
        run_case(label, text)
