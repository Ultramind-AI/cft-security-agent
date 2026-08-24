"""One CI entrypoint for discovery, sandbox lifecycle and the security gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

from app.config import settings
from app.pipeline_run import run_pipeline
from discovery.service import ProjectDiscovery
from evidence.telemetry import JsonRuntimeTelemetryStore
from executor.runtime_service_map import RuntimeServiceMapBuilder
from executor.sandbox_manager import SandboxManager
from pipeline.cancellation import (
    CancellationToken,
    RunCancelled,
    cancellation_scope,
    check_cancelled,
)
from pipeline.errors import error_from_exception
from pipeline.gate import evaluate_gate
from pipeline.subprocess_runner import run_cancellable_process
from pipeline.progress import PipelineProgressRecorder
from schemas.pipeline import GateResult
from schemas.target import TargetProfile

TECHNICAL_FAILURE_EXIT_CODE = 2
_SECRET_SUFFIXES = (
    "_API_KEY",
    "_PASSWORD",
    "_PRIVATE_KEY",
    "_SECRET",
    "_TOKEN",
)
_SECRET_NAMES = {
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_RUNTIME_TOKEN",
    "CI_JOB_JWT",
    "GITHUB_TOKEN",
}
_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover and start one trusted target, run SAST and bounded dynamic "
            "verification, then write FinalReport and Gate artifacts."
        )
    )
    parser.add_argument("--target", required=True, help="Full checked-out target repository")
    parser.add_argument(
        "--target-repository",
        help="GitHub owner/name, checked against profile metadata ci.repository",
    )
    parser.add_argument("--profile", required=True, help="Trusted TargetProfile YAML")
    parser.add_argument("--architecture", help="Optional trusted architecture YAML override")
    parser.add_argument("--architecture-overrides", help="Optional architecture overrides YAML")
    parser.add_argument("--sast-config", default="auto", help="Semgrep config/ruleset")
    parser.add_argument("--findings", help="Existing normalized findings JSON")
    parser.add_argument("--output-dir", default="artifacts/security-pipeline")
    parser.add_argument("--agent-mode", choices=("stub", "llm"), default=None)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--full-reports", action="store_true")
    parser.add_argument("--base-ref")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--base-findings")
    parser.add_argument("--base-architecture")
    return parser.parse_args()


def target_subprocess_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment that cannot expose CI/LLM credentials to target tools."""

    source = source or os.environ
    clean: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if upper in _SECRET_NAMES or upper.endswith(_SECRET_SUFFIXES):
            continue
        if upper.startswith(("CFT_LLM_", "CFT_AGENT_MODEL_")):
            continue
        clean[name] = value
    return clean


def _target_runner(environment: Mapping[str, str]):
    def run(
        argv: list[str],
        cwd: Path,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        # Compose получает только очищенное окружение, а не все секреты GitHub job.
        return run_cancellable_process(
            argv,
            cwd=cwd,
            environment=environment,
            timeout=timeout,
        )

    return run


def run_ci_pipeline(
    args: argparse.Namespace,
    *,
    discovery_service: ProjectDiscovery | None = None,
    sandbox_manager: SandboxManager | None = None,
    runtime_builder: RuntimeServiceMapBuilder | None = None,
    pipeline_runner: Callable[..., int] = run_pipeline,
) -> int:
    """Execute the complete CI flow and keep technical failures on exit code 2."""

    token: CancellationToken | None = getattr(args, "cancellation_token", None)
    with cancellation_scope(token):
        return _run_ci_pipeline_inner(
            args,
            discovery_service=discovery_service,
            sandbox_manager=sandbox_manager,
            runtime_builder=runtime_builder,
            pipeline_runner=pipeline_runner,
        )


def _run_ci_pipeline_inner(
    args: argparse.Namespace,
    *,
    discovery_service: ProjectDiscovery | None,
    sandbox_manager: SandboxManager | None,
    runtime_builder: RuntimeServiceMapBuilder | None,
    pipeline_runner: Callable[..., int],
) -> int:

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    progress = PipelineProgressRecorder(output_dir)
    settings_snapshot = {
        "target_file": settings.target_file,
        "target_repository_path": settings.target_repository_path,
        "evidence_dir": settings.evidence_dir,
        "executor_audit_log": settings.executor_audit_log,
        "executor_work_dir": settings.executor_work_dir,
        "agent_mode": settings.agent_mode,
    }

    try:
        check_cancelled()
        target = Path(args.target).expanduser().resolve(strict=True)
        if not target.is_dir():
            raise ValueError("--target must be a repository directory")

        profile_path = _trusted_profile_path(
            args.profile,
            require_project_profile=args.target_repository is not None,
        )
        base_profile = TargetProfile.from_yaml(
            profile_path,
            repository_path_override=target,
        )
        _validate_allowed_repository(base_profile, args.target_repository)
        discovery_api = discovery_service or ProjectDiscovery()
        check_cancelled()
        discovery = discovery_api.discover(target)
        check_cancelled()
        profile = discovery_api.build_profile(
            discovery,
            base_profile=base_profile,
        )
        _write_json(output_dir / "discovery.json", discovery.model_dump(mode="json"))
        _write_json(output_dir / "target-profile.json", profile.model_dump(mode="json"))
        progress.discovery_done(_discovery_summary(discovery))

        settings.target_file = profile_path
        settings.target_repository_path = target
        settings.evidence_dir = output_dir / "evidence"
        settings.executor_audit_log = output_dir / "audit" / "executor.jsonl"
        settings.executor_work_dir = output_dir / "workspaces"

        manager = sandbox_manager or SandboxManager(
            runner=_target_runner(target_subprocess_environment()),
            readiness_timeout=90.0,
        )
        builder = runtime_builder or RuntimeServiceMapBuilder()
        check_cancelled()
        progress.sandbox(status="running")
        session = manager.open(profile)

        with session:
            check_cancelled()
            runtime_services = builder.build(profile, session)
            _write_json(
                output_dir / "runtime-service-map.json",
                runtime_services.model_dump(mode="json"),
            )
            _write_json(output_dir / "sandbox-state.json", session.collect_state())
            if not runtime_services.services:
                diagnostics = "; ".join(
                    item.diagnostic for item in runtime_services.diagnostics
                )
                progress.sandbox(
                    status="failed",
                    detail=(
                        "no ready services" + (f": {diagnostics}" if diagnostics else "")
                    ),
                )
                raise RuntimeError(
                    "Sandbox has no ready services"
                    + (f": {diagnostics}" if diagnostics else "")
                )
            progress.sandbox(
                status="done",
                detail=f"{len(runtime_services.services)} services ready",
            )

            pipeline_args = _pipeline_args(args, profile_path=profile_path)
            check_cancelled()
            exit_code = pipeline_runner(
                pipeline_args,
                profile_override=profile,
                runtime_services=runtime_services,
            )
            check_cancelled()
            timeline = session.collect_telemetry(run_id=runtime_services.session_id)
            telemetry_ref, telemetry_path = JsonRuntimeTelemetryStore(
                output_dir / "telemetry"
            ).put(timeline)
            _write_json(
                output_dir / "telemetry-index.json",
                {
                    "session_id": session.session_id,
                    "artifact_ref": telemetry_ref,
                    "path": telemetry_path,
                },
            )

        _write_json(
            output_dir / "ci-summary.json",
            {
                "status": "completed" if exit_code in {0, 1} else "technical_failure",
                "target": profile.id,
                "adapter": session.adapter,
                "session_id": session.session_id,
                "exit_code": exit_code,
            },
        )
        return exit_code
    except RunCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - CI boundary converts failures into Gate
        progress.stage(
            "pipeline",
            status="failed",
            detail=type(exc).__name__,
        )
        return _write_technical_failure(output_dir, exc)
    finally:
        for name, value in settings_snapshot.items():
            setattr(settings, name, value)


def _pipeline_args(
    args: argparse.Namespace,
    *,
    profile_path: Path,
) -> argparse.Namespace:
    return argparse.Namespace(
        profile=str(profile_path),
        target=args.target,
        architecture=args.architecture,
        architecture_overrides=args.architecture_overrides,
        sast_config=args.sast_config,
        output_dir=str(Path(args.output_dir).expanduser().resolve()),
        findings=args.findings,
        agent_mode=args.agent_mode,
        max_iterations=args.max_iterations,
        full_reports=args.full_reports,
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        base_findings=args.base_findings,
        base_architecture=args.base_architecture,
        cancellation_token=getattr(args, "cancellation_token", None),
    )


def _validate_allowed_repository(
    profile: TargetProfile,
    repository: str | None,
) -> None:
    if repository is None:
        return
    if _REPOSITORY_NAME.fullmatch(repository) is None:
        raise ValueError("--target-repository must use the owner/name format")
    allowed = profile.metadata.get("ci.repository")
    if not allowed:
        raise ValueError("TargetProfile has no trusted metadata ci.repository")
    if repository.casefold() != allowed.casefold():
        raise ValueError("Checked-out repository does not match the trusted TargetProfile")


def _trusted_profile_path(value: str, *, require_project_profile: bool) -> Path:
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("--profile must be a YAML file")
    if require_project_profile:
        trusted_root = (Path.cwd() / "targets").resolve()
        try:
            path.relative_to(trusted_root)
        except ValueError as exc:
            raise ValueError("CI target profile must be stored under targets/") from exc
    return path


def _discovery_summary(discovery) -> str:
    components = getattr(discovery, "components", None) or []
    technologies: list[str] = []
    for component in components:
        for name in [
            *(getattr(component, "technologies", None) or []),
            *(getattr(component, "frameworks", None) or []),
        ]:
            if name not in technologies:
                technologies.append(name)
    summary = f"{len(components)} components"
    if technologies:
        summary += ": " + ", ".join(technologies[:6])
    return summary


def _write_technical_failure(output_dir: Path, exc: Exception) -> int:
    error = error_from_exception(
        exc,
        layer="pipeline",
        public_message="Managed CI pipeline failed",
    )
    gate = evaluate_gate([], errors=[error])
    _write_json(output_dir / "gate.json", gate.model_dump(mode="json"))
    _write_json(
        output_dir / "ci-summary.json",
        {
            "status": "technical_failure",
            "exit_code": TECHNICAL_FAILURE_EXIT_CODE,
            "error": error.model_dump(mode="json"),
        },
    )
    _print_technical_failure(gate)
    return TECHNICAL_FAILURE_EXIT_CODE


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _print_technical_failure(gate: GateResult) -> None:
    print("CI/CD GATE: FAIL (technical failure)")
    for error in gate.errors:
        print(f"- [{error.code}/{error.layer}] {error.message}")
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        print("::error::Managed security pipeline failed before a security verdict")


def main() -> int:
    return run_ci_pipeline(_parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
