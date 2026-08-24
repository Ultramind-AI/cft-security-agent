from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from api.registry import ApiTargetRegistry
from api.service import ProjectImportError, RunNotReadyError, RunOrchestrator
from api.store import ApiStore
from app.config import settings
from schemas.api import (
    ApiEvidence,
    ApiFinding,
    ApiProject,
    ApiRun,
    ChatSession,
    ChatSnapshot,
    CreateChatSessionRequest,
    CreateRunRequest,
    ImportProjectFilesRequest,
    RunDiscoveryView,
    RunProgress,
    RunTimeline,
    SendChatMessageRequest,
)
from schemas.pipeline import GateResult
from schemas.report import FinalReport


def create_app(orchestrator: RunOrchestrator | None = None) -> FastAPI:
    service = orchestrator or _default_orchestrator()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        service.close()

    app = FastAPI(
        title="CFT Security Agent API",
        version="1.1",
        lifespan=lifespan,
    )
    app.state.orchestrator = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/projects", response_model=list[ApiProject])
    def list_projects() -> list[ApiProject]:
        return service.list_projects()

    @app.post("/projects/import", response_model=ApiProject, status_code=status.HTTP_201_CREATED)
    async def import_project(
        request: Request,
        filename: str = Header(alias="X-Project-Filename", min_length=1, max_length=255),
    ) -> ApiProject:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > service.max_upload_bytes:
                raise HTTPException(status_code=413, detail="Project archive is too large")
        try:
            return await asyncio.to_thread(
                service.import_project_zip,
                filename=filename,
                content=bytes(content),
            )
        except ProjectImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/projects/import-files",
        response_model=ApiProject,
        status_code=status.HTTP_201_CREATED,
    )
    def import_project_files(request: ImportProjectFilesRequest) -> ApiProject:
        try:
            return service.import_project_files(request)
        except ProjectImportError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/runs", response_model=list[ApiRun])
    def list_runs(limit: int = Query(default=100, ge=1, le=500)) -> list[ApiRun]:
        return service.list_runs(limit=limit)

    @app.post("/runs", response_model=ApiRun, status_code=status.HTTP_202_ACCEPTED)
    def create_run(request: CreateRunRequest) -> ApiRun:
        try:
            return service.create_run(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown registered target") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/runs/{run_id}", response_model=ApiRun)
    def get_run(run_id: str) -> ApiRun:
        return _not_found(lambda: service.get_run(run_id), "Run not found")

    @app.get("/runs/{run_id}/events")
    async def stream_run_events(run_id: str, request: Request) -> StreamingResponse:
        _not_found(lambda: service.get_run(run_id), "Run not found")

        async def event_stream():
            last_payload: str | None = None
            while True:
                if await request.is_disconnected():
                    return
                run = await asyncio.to_thread(service.get_run, run_id)
                payload = run.model_dump(mode="json")
                encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                if encoded != last_payload:
                    yield f"event: run\ndata: {encoded}\n\n"
                    last_payload = encoded
                if run.status not in {"queued", "running"}:
                    yield f"event: done\ndata: {encoded}\n\n"
                    return
                await asyncio.sleep(0.75)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/chat/sessions", response_model=list[ChatSession])
    def list_chat_sessions(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[ChatSession]:
        return service.list_chat_sessions(limit=limit)

    @app.post(
        "/chat/sessions",
        response_model=ChatSession,
        status_code=status.HTTP_201_CREATED,
    )
    def create_chat_session(request: CreateChatSessionRequest) -> ChatSession:
        try:
            return service.create_chat_session(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown registered target") from exc
        except (ProjectImportError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/chat/sessions/{session_id}", response_model=ChatSnapshot)
    def get_chat_snapshot(session_id: str) -> ChatSnapshot:
        return _not_found(
            lambda: service.get_chat_snapshot(session_id),
            "Chat session not found",
        )

    @app.post("/chat/sessions/{session_id}/messages", response_model=ChatSnapshot)
    def send_chat_message(
        session_id: str,
        request: SendChatMessageRequest,
    ) -> ChatSnapshot:
        try:
            return service.send_chat_message(session_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Chat session not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/chat/sessions/{session_id}/events")
    async def stream_chat_events(session_id: str, request: Request) -> StreamingResponse:
        _not_found(
            lambda: service.get_chat_snapshot(session_id),
            "Chat session not found",
        )

        async def event_stream():
            last_payload: str | None = None
            while True:
                if await request.is_disconnected():
                    return
                snapshot = await asyncio.to_thread(service.get_chat_snapshot, session_id)
                payload = snapshot.model_dump(mode="json")
                encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                if encoded != last_payload:
                    yield f"event: snapshot\ndata: {encoded}\n\n"
                    last_payload = encoded
                run = snapshot.run
                if run is None or run.status not in {"queued", "running"}:
                    yield f"event: done\ndata: {encoded}\n\n"
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/runs/{run_id}/progress", response_model=RunProgress)
    def get_run_progress(run_id: str) -> RunProgress:
        return _not_found(lambda: service.get_run_progress(run_id), "Run not found")

    @app.get("/runs/{run_id}/discovery", response_model=RunDiscoveryView)
    def get_run_discovery(run_id: str) -> RunDiscoveryView:
        # Discovery is available as soon as the pipeline wrote it, even mid-run.
        return _not_found(lambda: service.get_run_discovery(run_id), "Discovery not found")

    @app.get("/runs/{run_id}/findings", response_model=list[ApiFinding])
    def list_findings(run_id: str) -> list[ApiFinding]:
        return _not_found(lambda: service.list_findings(run_id), "Run not found")

    @app.get("/runs/{run_id}/findings/{finding_id}", response_model=ApiFinding)
    def get_finding(run_id: str, finding_id: str) -> ApiFinding:
        return _not_found(
            lambda: service.get_finding(run_id, finding_id),
            "Finding not found",
        )

    @app.get("/runs/{run_id}/evidence", response_model=list[ApiEvidence])
    def list_evidence(run_id: str, finding_id: str | None = None) -> list[ApiEvidence]:
        return _not_found(
            lambda: service.list_evidence(run_id, finding_id=finding_id),
            "Run not found",
        )

    @app.get("/runs/{run_id}/timeline", response_model=RunTimeline)
    def get_timeline(run_id: str) -> RunTimeline:
        return _completed(lambda: service.get_timeline(run_id))

    @app.get("/runs/{run_id}/reports", response_model=list[FinalReport])
    def list_reports(run_id: str) -> list[FinalReport]:
        return _completed(lambda: service.list_reports(run_id))

    @app.get("/runs/{run_id}/reports/{finding_id}", response_model=FinalReport)
    def get_report(run_id: str, finding_id: str) -> FinalReport:
        return _completed(lambda: service.get_report(run_id, finding_id))

    @app.get("/runs/{run_id}/gate", response_model=GateResult)
    def get_gate(run_id: str) -> GateResult:
        return _completed(lambda: service.get_gate(run_id))

    return app


def _default_orchestrator() -> RunOrchestrator:
    profiles = [
        item.strip()
        for item in settings.api_target_profiles.split(",")
        if item.strip()
    ]
    registry = ApiTargetRegistry.from_profile_paths(
        profiles,
        trusted_root=Path("targets"),
    )
    return RunOrchestrator(
        registry=registry,
        store=ApiStore(settings.api_database_path),
        artifact_root=settings.api_artifact_root,
        project_root=settings.api_project_root,
        max_upload_bytes=settings.api_max_upload_bytes,
    )


def _not_found(call, detail: str):
    try:
        return call()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _completed(call):
    try:
        return call()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run or finding not found") from exc
    except RunNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def main() -> None:
    uvicorn.run(
        create_app(),
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
