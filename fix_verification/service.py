from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from executor.sandbox import _communicate_bounded
from security.error_redaction import redact_error_message
from schemas.evidence import Evidence
from schemas.fix import (
    FixCheck,
    FixCheckResult,
    FixVerificationArtifact,
    PatchApplicationResult,
    ProposedPatch,
)
from schemas.report import FinalReport

_MAX_OUTPUT = 16_384
_MAX_ARG_COUNT = 64
_MAX_ARG_LENGTH = 4096
_IGNORED_COPY_NAMES = {".git", ".venv", "node_modules", "dist", "build"}
_PATCH_FILE_HEADER = re.compile(r"^\+\+\+ (?:b/)?(.+)$", re.MULTILINE)
_BLOCKED_EXECUTABLES = {
    "bash",
    "busybox",
    "cmd",
    "curl",
    "docker",
    "ftp",
    "nc",
    "netcat",
    "podman",
    "powershell",
    "pwsh",
    "scp",
    "sh",
    "socat",
    "ssh",
    "sudo",
    "telnet",
    "wget",
    "zsh",
}
_BLOCKED_COMMAND_MARKERS = (
    "http://",
    "https://",
    "ftp://",
    "/var/run/docker.sock",
    "docker.sock",
    "docker_host",
    "import socket",
    "import urllib",
    "import requests",
    "import httpx",
    "import aiohttp",
)


class FixVerificationService:
    """Apply and test a proposed diff in an ephemeral copy of the target only."""

    def verify(
        self,
        *,
        report: FinalReport,
        proposal: ProposedPatch,
        target: str | Path,
        checks: list[FixCheck],
    ) -> FixVerificationArtifact:
        if report.status != "confirmed":
            raise ValueError("Fix verification is allowed only for a CONFIRMED finding")
        if proposal.finding_id != report.finding_id:
            raise ValueError("Patch proposal finding_id does not match the report")

        source = Path(target).expanduser().resolve()
        if not source.is_dir():
            raise ValueError(f"Target directory does not exist: {source}")

        public_checks = [_public_check(check) for check in checks]

        scope_error = _patch_scope_error(proposal.unified_diff, report.finding.file)
        if scope_error is not None:
            patch_application = PatchApplicationResult(
                status="rejected",
                detail=scope_error,
            )
            verdict, explanation = _fix_verdict(patch_application, checks, [])
            return FixVerificationArtifact(
                original_finding=report.finding,
                original_status=report.status,
                proposed_patch=proposal,
                patch_application=patch_application,
                re_test_actions=public_checks,
                verdict=verdict,
                explanation=explanation,
            )

        with tempfile.TemporaryDirectory(prefix="cft-fix-verification-") as temp_dir:
            workspace = Path(temp_dir) / "target"
            shutil.copytree(source, workspace, ignore=_ignore_copy_names)
            patch_application = _apply_patch(workspace, proposal.unified_diff)

            results: list[FixCheckResult] = []
            if patch_application.status == "applied":
                results = [_run_check(workspace, check) for check in checks]

        evidence = _build_evidence(report.finding_id, results)
        verdict, explanation = _fix_verdict(patch_application, checks, results)
        return FixVerificationArtifact(
            original_finding=report.finding,
            original_status=report.status,
            proposed_patch=proposal,
            patch_application=patch_application,
            re_test_actions=public_checks,
            re_test_results=results,
            new_evidence=evidence,
            verdict=verdict,
            explanation=explanation,
        )


def _ignore_copy_names(_directory: str, names: list[str]) -> set[str]:
    return set(names).intersection(_IGNORED_COPY_NAMES)


def _patch_scope_error(unified_diff: str, finding_file: str) -> str | None:
    paths = {
        value.replace("\\", "/")
        for value in _PATCH_FILE_HEADER.findall(unified_diff)
        if value != "/dev/null"
    }
    expected = finding_file.replace("\\", "/")
    while expected.startswith("./"):
        expected = expected[2:]
    if not paths:
        return "The proposal does not contain a target file in unified diff format."
    if paths != {expected}:
        return "The proposal changes files outside the original finding scope."
    return None


def _apply_patch(workspace: Path, unified_diff: str) -> PatchApplicationResult:
    patch_path = workspace.parent / "proposed.patch"
    patch_path.write_text(unified_diff, encoding="utf-8")
    check = _run_process(
        ["git", "apply", "--check", str(patch_path)],
        cwd=workspace,
        timeout_seconds=30,
    )
    if check.status != "passed":
        return PatchApplicationResult(
            status="rejected",
            detail=check.stderr or check.stdout or "git apply --check rejected the patch",
        )

    applied = _run_process(
        ["git", "apply", str(patch_path)],
        cwd=workspace,
        timeout_seconds=30,
    )
    if applied.status != "passed":
        return PatchApplicationResult(
            status="error",
            detail=applied.stderr or applied.stdout or "git apply failed",
        )
    return PatchApplicationResult(status="applied", detail="Applied in temporary workspace")


def _run_check(workspace: Path, check: FixCheck) -> FixCheckResult:
    policy_error = _check_argv_error(check.argv)
    if policy_error is not None:
        return FixCheckResult(
            id=check.id,
            kind=check.kind,
            argv=_public_argv(check.argv),
            status="error",
            stderr=policy_error,
        )
    result = _run_process(check.argv, cwd=workspace, timeout_seconds=check.timeout_seconds)
    return FixCheckResult(
        id=check.id,
        kind=check.kind,
        argv=_public_argv(check.argv),
        status=result.status,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _run_process(argv: list[str], *, cwd: Path, timeout_seconds: int) -> FixCheckResult:
    runtime_root = cwd.parent / ".fix-verification-runtime"
    home = runtime_root / "empty-home"
    config = runtime_root / "config"
    cache = runtime_root / "cache"
    tmp = runtime_root / "tmp"
    for directory in (home, config, cache, tmp):
        directory.mkdir(parents=True, exist_ok=True)

    safe_env = {
        # Do not inherit a user-managed PATH: it can point at wrappers or tools
        # which themselves read host credentials.  Absolute interpreters remain
        # usable, while fixed patch commands use the system default path.
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "XDG_CACHE_HOME": str(cache),
        "TMPDIR": str(tmp),
        "TMP": str(tmp),
        "TEMP": str(tmp),
        "DOCKER_CONFIG": str(config / "docker"),
        "DOCKER_HOST": "unix:///nonexistent/cft-fix-verification.sock",
        "NO_PROXY": "*",
        "no_proxy": "*",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=safe_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        raw_stdout, stdout_truncated, raw_stderr, stderr_truncated, timed_out = (
            _communicate_bounded(
                process,
                b"",
                timeout_seconds,
                _MAX_OUTPUT,
            )
        )
        if process.poll() is None:
            process.wait(timeout=1)
        exit_code = 124 if timed_out else (process.returncode or 0)
        if timed_out:
            raw_stderr = (
                f"Process timed out after {timeout_seconds}s\n".encode() + raw_stderr
            )
        return FixCheckResult(
            id="process",
            kind="static",
            argv=argv,
            status="timed_out" if timed_out else ("passed" if exit_code == 0 else "failed"),
            exit_code=exit_code,
            stdout=_public_output(_format_process_output(raw_stdout, stdout_truncated)),
            stderr=_public_output(_format_process_output(raw_stderr, stderr_truncated)),
        )
    except subprocess.TimeoutExpired as exc:
        return FixCheckResult(
            id="process",
            kind="static",
            argv=argv,
            status="timed_out",
            stdout=_public_output(exc.stdout or ""),
            stderr=_public_output(exc.stderr or ""),
        )
    except OSError as exc:
        return FixCheckResult(
            id="process",
            kind="static",
            argv=argv,
            status="error",
            stderr=_public_output(f"{type(exc).__name__}: {exc}"),
        )


def _format_process_output(value: bytes, truncated: bool) -> str:
    text = value.decode("utf-8", errors="replace")
    return f"{text}\n...[truncated]" if truncated else text


def _check_argv_error(argv: list[str]) -> str | None:
    if not argv or len(argv) > _MAX_ARG_COUNT:
        return "Fix re-test command is outside the bounded command policy."
    if any(
        not isinstance(argument, str)
        or not argument
        or len(argument) > _MAX_ARG_LENGTH
        or "\x00" in argument
        for argument in argv
    ):
        return "Fix re-test command contains an invalid argument."

    executable = Path(argv[0]).name.lower()
    if executable in _BLOCKED_EXECUTABLES:
        return f"Fix re-test executable '{executable}' is blocked by sandbox policy."

    command = " ".join(argv).lower()
    if any(marker in command for marker in _BLOCKED_COMMAND_MARKERS):
        return "Fix re-test command requests network or Docker access blocked by policy."
    return None


def _public_argv(argv: list[str]) -> list[str]:
    return [redact_error_message(argument, max_length=_MAX_ARG_LENGTH) for argument in argv]


def _public_check(check: FixCheck) -> FixCheck:
    return check.model_copy(update={"argv": _public_argv(check.argv)})


def _public_output(value: str | bytes) -> str:
    return _truncate(redact_error_message(value, max_length=_MAX_OUTPUT))


def _build_evidence(finding_id: str, results: list[FixCheckResult]) -> list[Evidence]:
    evidence: list[Evidence] = []
    for result in results:
        evidence.append(
            Evidence(
                id=f"fix-{finding_id}-{result.id}",
                action_id=result.id,
                type=f"fix_{result.kind}_result",
                summary=f"Fix re-test {result.id} finished with status {result.status}.",
                reliability="deterministic_subprocess",
                verdict=None,
                details={
                    "status": result.status,
                    "exit_code": result.exit_code,
                    "argv": result.argv,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
        )
    return evidence


def _fix_verdict(
    patch: PatchApplicationResult,
    checks: list[FixCheck],
    results: list[FixCheckResult],
) -> tuple[str, str]:
    if patch.status != "applied":
        return "not_verified", "The proposed patch could not be applied cleanly."
    if not checks:
        return "inconclusive", "The patch applied, but no re-test actions were supplied."
    if any(result.status in {"error", "timed_out"} for result in results):
        return "inconclusive", "At least one required re-test did not complete."
    if any(result.status == "failed" for result in results):
        return "not_verified", "At least one required re-test failed after patching."
    kinds = {result.kind for result in results}
    if not {"static", "runtime"}.issubset(kinds):
        return (
            "inconclusive",
            "Passing static and runtime re-tests are both required for verified-fix.",
        )
    return "verified", "The patch applied and all required static/runtime re-tests passed."


def _truncate(value: str | bytes) -> str:
    text = value.decode(errors="replace") if isinstance(value, bytes) else value
    return text[:_MAX_OUTPUT]
