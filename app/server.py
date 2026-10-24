"""Web app: paste a LinkedIn profile + conference, get a personal briefing and a chat agent.

Run: uv run uvicorn app.server:app --reload    then open http://localhost:8000
"""
import json
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import BaseModel

from app import matching, profile
from app.agent import MODEL, TOOLS, run_cypher, system_prompt
from app.db import get_graph

app = FastAPI()
STATIC = Path(__file__).resolve().parent / "static"
JOBS: dict[str, dict] = {}

STEPS = [
    "Checking the conference",
    "Scraping your LinkedIn profile with Bright Data",
    "Inferring your interests from your posts",
    "Adding you to the conference knowledge graph",
    "Matching you with speakers and talks",
    "Writing why you should meet each speaker",
]
UNSUPPORTED = ("Only GitHub Universe 2026 is supported for this demo. Its 162 talks and 183 speakers "
               "(plus 144 LinkedIn profiles) are already indexed with Bright Data.")


class AnalyzeRequest(BaseModel):
    linkedin_url: str
    conference: str


class ChatRequest(BaseModel):
    name: str
    messages: list[dict]


def _run(job_id: str, req: AnalyzeRequest):
    job = JOBS[job_id]

    def step(i, status, detail=None):
        job["steps"][i].update(status=status, detail=detail)

    try:
        step(0, "done", "GitHub Universe 2026 · Oct 28–29 · Fort Mason, San Francisco")
        step(1, "running")
        rec = profile.scrape(req.linkedin_url)
        step(1, "done", f"{rec['name']} · {rec.get('current_company_name') or (rec.get('current_company') or {}).get('name') or ''}")
        step(2, "running")
        interests = profile.infer_interests(rec)
        step(2, "done", ", ".join(i["topic"] for i in interests) or "No clear topics found in your posts")
        step(3, "running")
        g = get_graph()
        name = profile.add_to_graph(g, rec, interests)
        step(3, "done")
        step(4, "running")
        speakers = matching.speakers(g, name)
        job["result"] = {
            "name": name,
            "headline": rec.get("position") or rec.get("about", "")[:120],
            "avatar": rec.get("avatar"),
            "interests": interests,
            "speakers": speakers,
            "background": matching.shared_background(g, name),
            "schedule": matching.schedule(g, name),
            "graph": matching.connection_graph(name, speakers, interests),
        }
        step(4, "done", f"{len(speakers)} speakers, {sum(len(v) for v in job['result']['schedule'].values())} talks")
        step(5, "running")
        try:
            person = {"name": name, "headline": job["result"]["headline"],
                      "company": rec.get("current_company_name") or (rec.get("current_company") or {}).get("name"),
                      "schools": [e["title"] for e in rec.get("education") or [] if e.get("title")]}
            matching.add_reasons(person, interests, speakers)
            step(5, "done", f"with {matching.REASON_MODEL}")
        except Exception as e:  # the briefing still works without the prose
            step(5, "error", f"Skipped: {e}")
        job["status"] = "done"
    except Exception as e:
        running = next((s for s in job["steps"] if s["status"] == "running"), job["steps"][-1])
        running.update(status="error", detail=str(e))
        job["status"] = "error"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    if not profile.profile_slug(req.linkedin_url):
        raise HTTPException(400, "Enter a LinkedIn profile URL like https://www.linkedin.com/in/your-name")
    if not profile.is_supported_conference(req.conference):
        raise HTTPException(400, UNSUPPORTED)
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "running", "steps": [{"label": s, "status": "pending"} for s in STEPS], "result": None}
    threading.Thread(target=_run, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Unknown job")
    return JOBS[job_id]


@app.post("/api/chat")
def chat(req: ChatRequest):
    """One agent turn: the model may run several read-only Cypher queries before answering."""
    graph, client = get_graph(), OpenAI()
    messages = [{"role": "system", "content": system_prompt(graph)
                 + f"\n\nYou are talking to the attendee {req.name!r}; 'me'/'I' means them."}]
    messages += [{"role": m["role"], "content": m["content"]} for m in req.messages if m.get("role") in ("user", "assistant")]
    queries = []
    for _ in range(10):
        msg = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS).choices[0].message
        messages.append(msg)
        if not msg.tool_calls:
            return {"answer": msg.content, "queries": queries}
        for call in msg.tool_calls:
            query = json.loads(call.function.arguments)["query"]
            queries.append(query)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": run_cypher(graph, query)})
    return {"answer": "I ran out of steps answering that — try a more specific question.", "queries": queries}
