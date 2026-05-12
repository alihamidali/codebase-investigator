import asyncio
import json
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from auditor import run_audit
from github_utils import clone_repo, parse_github_url
from investigator import run_investigation
from session import create_session, get_session

load_dotenv()

app = FastAPI(title="Codebase Investigator")
app.mount("/static", StaticFiles(directory="static"), name="static")


class CloneRequest(BaseModel):
    url: str


class AskRequest(BaseModel):
    question: str


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.post("/api/clone")
async def clone_repository(body: CloneRequest):
    try:
        owner, repo = parse_github_url(body.url)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    try:
        repo_path = await asyncio.to_thread(clone_repo, body.url)
    except Exception as e:
        raise HTTPException(500, detail=f"Clone failed: {e}")

    clean_url = f"https://github.com/{owner}/{repo}"
    session = create_session(clean_url, f"{owner}/{repo}", repo_path)

    structure = await asyncio.to_thread(session.tools.get_structure, 2)
    return {
        "session_id": session.id,
        "repo_url": clean_url,
        "repo_name": f"{owner}/{repo}",
        "structure_preview": structure.get("structure", "")[:800],
    }


@app.post("/api/ask/{session_id}")
async def ask_question(session_id: str, body: AskRequest):
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, detail="Session not found")

    messages = session.get_investigator_messages(body.question)
    claims_summary = session.get_claims_summary()

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def run_thread():
        run_investigation(session.tools, messages, claims_summary, queue, loop)

    thread = threading.Thread(target=run_thread, daemon=True)
    thread.start()

    async def event_stream():
        answer_text = ""
        current_question = body.question

        try:
            while True:
                try:
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=180)
                except asyncio.TimeoutError:
                    yield _sse("error", {"message": "Investigation timed out after 3 minutes"})
                    yield _sse("complete", {})
                    return

                yield _sse(event_type, data)

                if event_type == "token":
                    answer_text += data.get("text", "")
                elif event_type == "answer_complete":
                    answer_text = data.get("text", answer_text)
                    break
                elif event_type in ("error", "done"):
                    if not answer_text:
                        yield _sse("complete", {})
                        return
                    break

        except Exception as e:
            yield _sse("error", {"message": str(e)})
            yield _sse("complete", {})
            return

        if answer_text:
            try:
                audit = await asyncio.to_thread(run_audit, session, current_question, answer_text)
                session.add_turn(current_question, answer_text, audit)
                yield _sse("audit", audit)
            except Exception as e:
                yield _sse("audit", {
                    "trust_level": "UNKNOWN",
                    "error": str(e),
                    "summary": "Audit failed.",
                    "citation_verification": [],
                })

        yield _sse("complete", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/file/{session_id}")
async def get_file(
    session_id: str,
    path: str = Query(...),
    start: int = Query(1),
    end: int = Query(None),
):
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, detail="Session not found")

    result = await asyncio.to_thread(session.tools.read_file, path, start, end)
    if "error" in result:
        raise HTTPException(404, detail=result["error"])
    return result


@app.get("/api/session/{session_id}")
async def session_info(session_id: str):
    session = get_session(session_id)
    if not session:
        raise HTTPException(404, detail="Session not found")
    return {
        "id": session.id,
        "repo_url": session.repo_url,
        "repo_name": session.repo_name,
        "turn_count": session.turn_count,
        "claims_count": len(session.claims_log),
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
