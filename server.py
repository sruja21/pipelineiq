
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import json

from analyzer import PipelineAnalyzer

app      = FastAPI(title="PipelineIQ API", version="1.0.0")
analyzer = PipelineAnalyzer()   # Singleton — shared across requests


# ── Request models ────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    log_text:      str
    pipeline_type: str  = "github-actions"
    stream:        bool = False

class ChatRequest(BaseModel):
    message:       str
    history:       list = []
    log_context:   str  = ""
    pipeline_type: str  = ""

class TeachRequest(BaseModel):
    log_text:      str
    pipeline_type: str
    root_cause:    str
    error_type:    str
    fix_applied:   str
    fix_commands:  list = []
    tags:          list = []


@app.get("/health")
def health():
    return {"status": "ok", "ollama": analyzer.llm.is_running()}


@app.get("/stats")
def stats():
    return analyzer.stats()


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    """
    Analyze a CI/CD failure log.
    Set stream=true to receive token-by-token SSE stream.
    """
    if not req.log_text.strip():
        raise HTTPException(400, "log_text is required")

    if req.stream:
        def gen():
            for token in analyzer.analyze_stream(req.log_text, req.pipeline_type):
                yield token
        return StreamingResponse(gen(), media_type="text/plain")

    return analyzer.analyze(req.log_text, req.pipeline_type)


@app.post("/chat")
def chat(req: ChatRequest):
    """Follow-up Q&A — streaming response."""
    def gen():
        for token in analyzer.chat(req.message, req.history, req.log_context, req.pipeline_type):
            yield token
    return StreamingResponse(gen(), media_type="text/plain")


@app.post("/teach")
def teach(req: TeachRequest):
    """Record a resolved failure to improve future analysis."""
    fid = analyzer.record_fix(req.log_text, req.pipeline_type, {
        "root_cause":   req.root_cause,
        "error_type":   req.error_type,
        "fix_applied":  req.fix_applied,
        "fix_commands": req.fix_commands,
        "tags":         req.tags,
    })
    return {"failure_id": fid, "kb_size": analyzer.rag.store.count()}


@app.post("/webhook/github")
async def github_webhook(payload: dict):
    """
    Auto-trigger from GitHub Actions.
    Configure at: Settings → Webhooks → Workflow runs
    """
    run = payload.get("workflow_run", {})
    if payload.get("action") == "completed" and run.get("conclusion") == "failure":
        return {
            "received": True,
            "run_id":   run.get("id"),
            "hint":     "Fetch logs from GitHub API then POST to /analyze"
        }
    return {"ignored": True}


@app.post("/webhook/jenkins")
async def jenkins_webhook(payload: dict):
    """
    Auto-trigger from Jenkins Generic Webhook Trigger plugin.
    """
    build = payload.get("build", {})
    if build.get("phase") == "FINALIZED" and build.get("status") == "FAILURE":
        return {"received": True, "build": build.get("full_url")}
    return {"ignored": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
