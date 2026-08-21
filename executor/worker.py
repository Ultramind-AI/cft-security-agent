"""Фиксированный worker для capability, не являющийся интерпретатором команд."""

import ast
import json
import re
import sys
import warnings
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

_DOCKERFILE_USER_TOOL = "inspect_dockerfile_user"
_PYTHON_PASSWORD_TOOL = "inspect_python_password_assignment"
_REACT_HTML_FLOW_TOOL = "inspect_react_dangerous_html_flow"
_MAX_SOURCE_BYTES = 512 * 1024
_MAX_ARTIFACTS = 64


def _read_payload() -> dict:
    data = sys.stdin.buffer.read(128 * 1024)
    if not data:
        raise ValueError("Missing sandbox request")
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Sandbox request must be an object")
    return payload


def _fixed_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid trusted target URL")
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _http_get(url: str, timeout: float, output_limit: int) -> tuple[int, str, str]:
    request = Request(
        url,
        headers={"User-Agent": "cft-security-agent-executor/0.4"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(output_limit + 1)
            text = body[:output_limit].decode("utf-8", errors="replace")
            if len(body) > output_limit:
                text = f"{text}\n...[truncated]"
            if 200 <= int(response.status) < 300:
                return 0, text, ""
            return 1, text, f"Unexpected HTTP status: {response.status}"
    except HTTPError as exc:
        body = exc.read(output_limit + 1)
        text = body[:output_limit].decode("utf-8", errors="replace")
        return 1, text, f"HTTP request failed with status {exc.code}"
    except URLError as exc:
        return 1, "", f"HTTP request failed: {exc.reason}"


def _safe_noop(parameters: dict) -> tuple[int, str, str]:
    allowed = {"message", "test_outcome"}
    unexpected = sorted(set(parameters) - allowed)
    if unexpected:
        return 2, "", f"Unsupported safe_noop parameters: {unexpected}"

    message = str(parameters.get("message", "ok"))[:256]
    outcome = str(parameters.get("test_outcome", "confirmed"))
    if outcome not in {"confirmed", "rejected", "inconclusive"}:
        return 2, "", "Invalid safe_noop test_outcome"
    return 0, f"safe_noop:{message}:outcome={outcome}", ""


def _validated_artifacts(raw_artifacts: object) -> dict[str, dict[str, str]]:
    if not isinstance(raw_artifacts, dict):
        raise TypeError("Trusted target artifacts must be an object")
    if len(raw_artifacts) > _MAX_ARTIFACTS:
        raise ValueError("Trusted target artifact registry exceeds limit")

    artifacts: dict[str, dict[str, str]] = {}
    for raw_id, raw_definition in raw_artifacts.items():
        artifact_id = str(raw_id).strip()
        if not artifact_id or not isinstance(raw_definition, dict):
            raise ValueError("Malformed trusted target artifact")
        kind = str(raw_definition.get("kind", "")).strip().lower()
        relative_path = str(raw_definition.get("path", "")).replace("\\", "/").strip()
        parsed_path = PurePosixPath(relative_path)
        if not kind or not relative_path or parsed_path.is_absolute() or ".." in parsed_path.parts:
            raise ValueError(f"Malformed trusted target artifact: {artifact_id}")
        artifacts[artifact_id] = {"kind": kind, "path": parsed_path.as_posix()}
    return artifacts


def _read_artifact(
    *,
    repository_path: str,
    artifacts: dict[str, dict[str, str]],
    artifact_id: str,
    expected_kind: str,
) -> tuple[str, str]:
    if not repository_path:
        raise ValueError("Trusted target repository path is not configured")
    if artifact_id not in artifacts:
        raise ValueError(f"Unknown trusted artifact id: {artifact_id}")

    definition = artifacts[artifact_id]
    if definition["kind"] != expected_kind:
        raise ValueError(
            f"Artifact '{artifact_id}' has kind '{definition['kind']}', expected '{expected_kind}'"
        )

    try:
        root = Path(repository_path).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("Trusted target repository is unavailable") from exc

    relative_path = definition["path"]
    try:
        source_file = (root / relative_path).resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"Trusted artifact is unavailable: {artifact_id}") from exc

    try:
        source_file.relative_to(root)
    except ValueError as exc:
        raise ValueError("Trusted artifact or symlink escaped target repository") from exc

    if not source_file.is_file():
        raise ValueError(f"Trusted artifact is not a regular file: {artifact_id}")
    if source_file.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError(f"Trusted artifact exceeds verification size limit: {artifact_id}")

    return relative_path, source_file.read_text(encoding="utf-8")


def _require_parameters(parameters: dict, required: set[str]) -> None:
    unexpected = sorted(set(parameters) - required)
    missing = sorted(required - set(parameters))
    if unexpected:
        raise ValueError(f"Unsupported capability parameters: {unexpected}")
    if missing:
        raise ValueError(f"Missing capability parameters: {missing}")
    if any(
        not isinstance(parameters[name], str) or not parameters[name].strip()
        for name in required
    ):
        raise ValueError("Capability parameters must be non-empty strings")


def _final_stage_user(dockerfile_text: str) -> tuple[int, str | None, int | None]:
    stage_count = 0
    final_user: str | None = None
    final_user_line: int | None = None

    for line_number, raw_line in enumerate(dockerfile_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        instruction = parts[0].upper()
        argument = parts[1].strip() if len(parts) == 2 else ""

        if instruction == "FROM":
            if not argument:
                raise ValueError("Malformed FROM instruction in Dockerfile")
            stage_count += 1
            final_user = None
            final_user_line = None
            continue

        if instruction == "USER" and stage_count > 0:
            if not argument:
                raise ValueError("Malformed USER instruction in Dockerfile")
            final_user = argument
            final_user_line = line_number

    if stage_count == 0:
        raise ValueError("Dockerfile contains no FROM instruction")

    return stage_count, final_user, final_user_line


def _classify_docker_user(user: str | None) -> str:
    if user is None:
        return "missing"
    normalized = user.strip().split(":", 1)[0].strip()
    if not normalized or "$" in normalized:
        return "dynamic"
    if normalized.lower() == "root" or normalized == "0":
        return "root"
    return "non_root"


def _inspect_dockerfile_user(
    repository_path: str,
    artifacts: dict[str, dict[str, str]],
    parameters: dict,
) -> tuple[int, str, str]:
    _require_parameters(parameters, {"artifact_id"})
    artifact_id = str(parameters["artifact_id"])
    relative_path, dockerfile_text = _read_artifact(
        repository_path=repository_path,
        artifacts=artifacts,
        artifact_id=artifact_id,
        expected_kind="dockerfile",
    )
    final_stage, user, user_line = _final_stage_user(dockerfile_text)
    classification = _classify_docker_user(user)

    if classification in {"missing", "root"}:
        verdict = "confirmed"
        explanation = (
            "The final Dockerfile stage does not establish an explicit non-root USER. "
            "This confirms the reported source hardening condition only; "
            "runtime UID was not checked."
        )
    elif classification == "non_root":
        verdict = "rejected"
        explanation = (
            "The final Dockerfile stage explicitly establishes a non-root USER, so the reported "
            "missing/root USER source condition is not present."
        )
    else:
        verdict = "inconclusive"
        explanation = (
            "The final Dockerfile USER is dynamic, so source inspection cannot determine whether "
            "the effective runtime user is root."
        )

    result = {
        "schema": "cft.dockerfile_user_check.v2",
        "artifact_id": artifact_id,
        "dockerfile": relative_path,
        "final_stage": final_stage,
        "user_directive_present": user is not None,
        "user": user,
        "user_line": user_line,
        "user_classification": classification,
        "verdict": verdict,
        "scope": "source",
        "runtime_user_verified": False,
        "explanation": explanation,
    }
    return 0, json.dumps(result, ensure_ascii=False, separators=(",", ":")), ""


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _hardcoded_password_records(tree: ast.AST) -> tuple[int, int]:
    hardcoded = 0
    privileged = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        values: dict[str, ast.AST] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=False):
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                values[key_node.value] = value_node
        password_node = values.get("password")
        if not isinstance(password_node, ast.Constant) or not isinstance(password_node.value, str):
            continue
        hardcoded += 1
        if any(
            isinstance(values.get(flag), ast.Constant) and values[flag].value is True
            for flag in ("is_superuser", "is_staff")
            if flag in values
        ):
            privileged += 1
    return hardcoded, privileged


def _inspect_python_password_assignment(
    repository_path: str,
    artifacts: dict[str, dict[str, str]],
    parameters: dict,
) -> tuple[int, str, str]:
    _require_parameters(parameters, {"artifact_id"})
    artifact_id = str(parameters["artifact_id"])
    relative_path, source = _read_artifact(
        repository_path=repository_path,
        artifacts=artifacts,
        artifact_id=artifact_id,
        expected_kind="python",
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        raise ValueError(f"Python source could not be parsed: line {exc.lineno}") from exc

    set_password_calls = 0
    validate_password_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node)
        if call_name == "set_password":
            set_password_calls += 1
        elif call_name == "validate_password":
            validate_password_calls += 1

    hardcoded_password_literals, privileged_hardcoded_password_records = (
        _hardcoded_password_records(tree)
    )

    if set_password_calls == 0:
        verdict = "rejected"
        explanation = "No set_password call is present in the trusted Python artifact."
    elif validate_password_calls == 0:
        verdict = "confirmed"
        explanation = (
            "The trusted Python artifact sets passwords without a validate_password call. "
            "Any password literal values are deliberately redacted from Evidence."
        )
    else:
        verdict = "inconclusive"
        explanation = (
            "Both password assignment and password validation calls are present; this bounded "
            "source check does not infer whether every assignment is validated."
        )

    result = {
        "schema": "cft.python_password_assignment_check.v1",
        "artifact_id": artifact_id,
        "file": relative_path,
        "set_password_calls": set_password_calls,
        "validate_password_calls": validate_password_calls,
        "hardcoded_password_literals": hardcoded_password_literals,
        "privileged_hardcoded_password_records": privileged_hardcoded_password_records,
        "password_values_redacted": True,
        "verdict": verdict,
        "scope": "source",
        "runtime_auth_verified": False,
        "explanation": explanation,
    }
    return 0, json.dumps(result, ensure_ascii=False, separators=(",", ":")), ""


def _attribute_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _attribute_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _literal_string_collection(node: ast.AST) -> set[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value == "__all__":
            return {"__all__"}
        return {node.value}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: set[str] = set()
        for item in node.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            values.add(item.value)
        return values
    return None


def _find_class(tree: ast.AST, name: str) -> ast.ClassDef | None:
    return next(
        (
            node
            for node in getattr(tree, "body", [])
            if isinstance(node, ast.ClassDef) and node.name == name
        ),
        None,
    )


def _model_field_found(tree: ast.AST, *, class_name: str, field_name: str) -> bool:
    class_node = _find_class(tree, class_name)
    if class_node is None:
        return False
    for node in class_node.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (
            isinstance(target, ast.Name)
            and target.id == field_name
            and isinstance(node.value, ast.Call)
        ):
            return True
    return False


def _serializer_field_state(
    tree: ast.AST,
    *,
    serializer_name: str,
    field_name: str,
) -> tuple[bool | None, bool | None]:
    serializer = _find_class(tree, serializer_name)
    if serializer is None:
        return None, None
    meta = next(
        (
            node
            for node in serializer.body
            if isinstance(node, ast.ClassDef) and node.name == "Meta"
        ),
        None,
    )
    if meta is None:
        return None, None

    fields: set[str] | None = None
    read_only: set[str] = set()
    for node in meta.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id == "fields":
            fields = _literal_string_collection(node.value)
        elif target.id == "read_only_fields":
            parsed = _literal_string_collection(node.value)
            if parsed is not None:
                read_only = parsed

    exposed = None if fields is None else "__all__" in fields or field_name in fields
    return exposed, field_name in read_only


def _viewset_update_state(
    tree: ast.AST,
    *,
    viewset_name: str,
    serializer_name: str,
) -> tuple[bool, bool | None]:
    viewset = _find_class(tree, viewset_name)
    if viewset is None:
        return False, None

    model_viewset = any(_attribute_name(base).endswith("ModelViewSet") for base in viewset.bases)
    serializer_match = False
    permission_names: set[str] = set()
    for node in viewset.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if target.id == "serializer_class":
            serializer_match = _attribute_name(node.value).endswith(serializer_name)
        elif target.id == "permission_classes" and isinstance(node.value, (ast.List, ast.Tuple)):
            permission_names.update(_attribute_name(item) for item in node.value.elts)

    authenticated = None
    if any(name.endswith("IsAuthenticated") for name in permission_names):
        authenticated = True
    elif any(name.endswith("AllowAny") for name in permission_names):
        authenticated = False

    return model_viewset and serializer_match, authenticated


def _react_sink_state(source: str, *, field_name: str) -> tuple[bool, str | None, bool]:
    pattern = re.compile(
        r"dangerouslySetInnerHTML\s*=\s*\{\{\s*__html\s*:\s*(?P<expr>[^}]+)\}\}",
        flags=re.MULTILINE,
    )
    sanitizer_pattern = re.compile(r"(?:DOMPurify\.)?sanitize\s*\(", flags=re.IGNORECASE)
    matches = list(pattern.finditer(source))
    if not matches:
        return False, None, False

    relevant = next(
        (
            match
            for match in matches
            if re.search(rf"\b{re.escape(field_name)}\b", match.group("expr"))
        ),
        matches[0],
    )
    expression = " ".join(relevant.group("expr").strip().split())[:300]
    return True, expression, sanitizer_pattern.search(expression) is not None


def _parse_artifact_python(
    repository_path: str,
    artifacts: dict[str, dict[str, str]],
    artifact_id: str,
) -> tuple[str, ast.AST]:
    relative_path, source = _read_artifact(
        repository_path=repository_path,
        artifacts=artifacts,
        artifact_id=artifact_id,
        expected_kind="python",
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=relative_path)
        return relative_path, tree
    except SyntaxError as exc:
        raise ValueError(
            f"Python source could not be parsed: {relative_path}:{exc.lineno}"
        ) from exc


def _inspect_react_dangerous_html_flow(
    repository_path: str,
    artifacts: dict[str, dict[str, str]],
    parameters: dict,
) -> tuple[int, str, str]:
    required = {
        "frontend_artifact_id",
        "model_artifact_id",
        "serializer_artifact_id",
        "view_artifact_id",
        "field",
    }
    _require_parameters(parameters, required)
    field_name = str(parameters["field"])
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", field_name):
        raise ValueError("Field parameter is not a safe identifier")

    frontend_id = str(parameters["frontend_artifact_id"])
    frontend_path, frontend_source = _read_artifact(
        repository_path=repository_path,
        artifacts=artifacts,
        artifact_id=frontend_id,
        expected_kind="javascript",
    )
    sink_found, sink_expression, sanitizer_detected = _react_sink_state(
        frontend_source,
        field_name=field_name,
    )

    model_path, model_tree = _parse_artifact_python(
        repository_path,
        artifacts,
        str(parameters["model_artifact_id"]),
    )
    serializer_path, serializer_tree = _parse_artifact_python(
        repository_path,
        artifacts,
        str(parameters["serializer_artifact_id"]),
    )
    view_path, view_tree = _parse_artifact_python(
        repository_path,
        artifacts,
        str(parameters["view_artifact_id"]),
    )

    model_field = _model_field_found(model_tree, class_name="User", field_name=field_name)
    serializer_exposed, serializer_read_only = _serializer_field_state(
        serializer_tree,
        serializer_name="UserSerializer",
        field_name=field_name,
    )
    update_route, authentication_required = _viewset_update_state(
        view_tree,
        viewset_name="UserViewSet",
        serializer_name="UserSerializer",
    )

    field_reference = sink_expression is not None and bool(
        re.search(rf"(?:^|\.){re.escape(field_name)}\b", sink_expression)
    )
    writable = (
        model_field
        and serializer_exposed is True
        and serializer_read_only is False
        and update_route
    )

    if not sink_found or sanitizer_detected or serializer_read_only is True:
        verdict = "rejected"
        explanation = (
            "The bounded static source-flow check did not find an unsanitized writable-field path "
            "to dangerouslySetInnerHTML."
        )
    elif field_reference and writable:
        verdict = "confirmed"
        explanation = (
            "Static source flow confirms that a writable user field reaches "
            "dangerouslySetInnerHTML "
            "without an obvious sanitizer at the sink. No browser payload was executed."
        )
    else:
        verdict = "inconclusive"
        explanation = (
            "A dangerous HTML sink is present, but the bounded source-flow check could not "
            "prove all "
            "required writable-field links."
        )

    result = {
        "schema": "cft.react_dangerous_html_flow_check.v1",
        "frontend_artifact_id": frontend_id,
        "frontend_file": frontend_path,
        "supporting_files": [model_path, serializer_path, view_path],
        "field": field_name,
        "dangerous_html_sink_found": sink_found,
        "sink_expression": sink_expression,
        "sanitizer_detected": sanitizer_detected,
        "model_field_found": model_field,
        "serializer_field_exposed": serializer_exposed,
        "serializer_field_read_only": serializer_read_only,
        "model_viewset_update_route": update_route,
        "authentication_required": authentication_required,
        "verdict": verdict,
        "scope": "static_source_flow",
        "browser_execution_verified": False,
        "explanation": explanation,
    }
    return 0, json.dumps(result, ensure_ascii=False, separators=(",", ":")), ""


def _execute(payload: dict) -> tuple[int, str, str]:
    tool = str(payload.get("tool", ""))
    base_url = str(payload.get("base_url", ""))
    repository_path = str(payload.get("repository_path", ""))
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict):
        return 2, "", "Capability parameters must be an object"

    timeout = float(payload.get("request_timeout_seconds", 1.0))
    output_limit = int(payload.get("max_output_bytes", 16_384))

    if tool == "safe_noop":
        return _safe_noop(parameters)

    if tool in {_DOCKERFILE_USER_TOOL, _PYTHON_PASSWORD_TOOL, _REACT_HTML_FLOW_TOOL}:
        try:
            artifacts = _validated_artifacts(payload.get("artifacts", {}))
            if tool == _DOCKERFILE_USER_TOOL:
                return _inspect_dockerfile_user(repository_path, artifacts, parameters)
            if tool == _PYTHON_PASSWORD_TOOL:
                return _inspect_python_password_assignment(repository_path, artifacts, parameters)
            return _inspect_react_dangerous_html_flow(repository_path, artifacts, parameters)
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            return 1, "", f"Source verification failed: {type(exc).__name__}: {exc}"

    if parameters:
        return 2, "", f"{tool} does not accept ActionProposal parameters"

    if tool == "check_sberlab_health":
        exit_code, stdout, stderr = _http_get(
            _fixed_url(base_url, "/health/"),
            timeout,
            output_limit,
        )
        if exit_code != 0:
            return exit_code, stdout, stderr
        try:
            health = json.loads(stdout)
        except json.JSONDecodeError:
            return 1, stdout, "Health endpoint returned invalid JSON"
        if health.get("status") != "ok" or health.get("database") != "ok":
            return 1, stdout, "SberLab health response is not ready"
        return 0, stdout, ""

    if tool == "get_sberlab_public_projects":
        return _http_get(
            _fixed_url(base_url, "/api/projects/"),
            timeout,
            output_limit,
        )

    return 126, "", f"Unknown worker capability: {tool}"


def main() -> int:
    try:
        exit_code, stdout, stderr = _execute(_read_payload())
    except (OSError, TypeError, ValueError) as exc:
        exit_code = 1
        stdout = ""
        stderr = f"Worker failed: {type(exc).__name__}: {exc}"

    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, file=sys.stderr, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
