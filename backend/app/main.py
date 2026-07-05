"""
FastAPI wrapper around the LangGraph pipeline. /generate returns immediately with a
job_id and runs the pipeline in a background task -- the OfficeSkill node alone can
take ~10 minutes, so the HTTP request can't hold that connection open (see jobs.py's
scope note on why this is an in-memory store, not a real queue).
"""
import time
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.graph import doc_gen_graph
from app.jobs import create_job, get_job, list_jobs, update_job

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

app = FastAPI(title="AI Document Platform")


class GenerateRequest(BaseModel):
    request: str


class GenerateAcceptedResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    status: str  # pending | running | completed | failed
    current_step: Optional[str] = None
    request: Optional[str] = None
    format: Optional[str] = None
    title: Optional[str] = None
    validation: Optional[dict] = None
    size_bytes: Optional[int] = None
    created_at: Optional[float] = None
    finished_at: Optional[float] = None
    download_ready: bool = False
    error: Optional[str] = None


def _run_job(job_id: str, request_text: str) -> None:
    update_job(job_id, status="running")
    state: dict = {"request": request_text}
    try:
        for step in doc_gen_graph.stream({"request": request_text}, stream_mode="updates"):
            node_name, node_output = next(iter(step.items()))
            state.update(node_output)
            update_job(job_id, current_step=node_name)

        validation = state.get("validation", {})
        if state.get("error") or not validation.get("ok"):
            update_job(
                job_id,
                status="failed",
                finished_at=time.time(),
                error=state.get("error") or validation.get("details", "validation failed"),
            )
            return

        file_path = state.get("download_path")
        update_job(
            job_id,
            status="completed",
            current_step="export",
            finished_at=time.time(),
            result={
                "format": state.get("format"),
                "title": state.get("content_plan", {}).get("title"),
                "validation": validation,
                "file_path": file_path,
                "size_bytes": Path(file_path).stat().st_size if file_path else None,
            },
        )
    except Exception as e:
        update_job(job_id, status="failed", finished_at=time.time(), error=str(e))


@app.post("/generate", response_model=GenerateAcceptedResponse, status_code=202)
def generate(body: GenerateRequest, background_tasks: BackgroundTasks) -> GenerateAcceptedResponse:
    job_id = create_job(body.request)
    background_tasks.add_task(_run_job, job_id, body.request)
    return GenerateAcceptedResponse(job_id=job_id)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str) -> JobStatusResponse:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    result = job.get("result") or {}
    return JobStatusResponse(
        status=job["status"],
        current_step=job.get("current_step"),
        request=job.get("request"),
        format=result.get("format"),
        title=result.get("title"),
        validation=result.get("validation"),
        size_bytes=result.get("size_bytes"),
        created_at=job.get("created_at"),
        finished_at=job.get("finished_at"),
        download_ready=job["status"] == "completed",
        error=job.get("error"),
    )


@app.get("/jobs/{job_id}/download")
def job_download(job_id: str):
    job = get_job(job_id)
    if job is None or job["status"] != "completed":
        raise HTTPException(status_code=404, detail="file not ready")
    path = job["result"]["file_path"]
    return FileResponse(path, filename=Path(path).name)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC_DIR / "index.html").read_text()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/jobs")
def jobs_list() -> dict:
    """
    Backs the UI's "Recent generations" panel with real, session-scoped data (see
    jobs.py's scope note -- in-memory, does not survive a restart). Also usable as a
    plain debug endpoint.
    """
    return list_jobs()
