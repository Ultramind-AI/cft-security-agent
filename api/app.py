from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query, status

from api.registry import ApiTargetRegistry
from api.service import RunNotReadyError, RunOrchestrator
from api.store import ApiStore
from app.config import settings
from schemas.api import ApiEvidence, ApiFinding, ApiProject, ApiRun, CreateRunRequest, RunTimeline
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
        version="1.0",
        lifespan=lifespan,
    )
    app.state.orchestrator = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/projects", response_model=list[ApiProject])
    def list_projects() -> list[ApiProject]:
        return service.list_projects()

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
    )


def _not_found(call, detail: str):
    try:
        return call()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=detail) from exc


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
