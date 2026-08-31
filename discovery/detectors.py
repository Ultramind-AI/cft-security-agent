from __future__ import annotations

import json
import re
import tomllib
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import yaml

from discovery.base import DiscoveryContext
from schemas.discovery import DiscoverySignal

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
_HEALTH_URL = re.compile(r"https?://[^\s'\";,)]+")
_COMPOSE_FILES = {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}
_PYTHON_FRAMEWORK_PACKAGES = {
    "celery": "celery",
    "django": "django",
    "fastapi": "fastapi",
    "flask": "flask",
}
_NODE_FRAMEWORK_PACKAGES = {
    "@angular/core": "angular",
    "@nestjs/core": "nestjs",
    "express": "express",
    "next": "next",
    "react": "react",
    "svelte": "svelte",
    "vite": "vite",
    "vue": "vue",
}


def _parent(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "." if parent == "." else parent


def _signal(
    detector: str,
    kind: str,
    path: str,
    root: str | None,
    *,
    name: str | None = None,
    value: str | None = None,
    command: list[str] | None = None,
    confidence: float,
    anchor: bool = False,
    metadata: dict[str, str] | None = None,
) -> DiscoverySignal:
    return DiscoverySignal(
        detector=detector,
        kind=kind,
        path=path,
        component_root=root,
        name=name,
        value=value,
        command=command or [],
        confidence=confidence,
        anchor=anchor,
        metadata=metadata or {},
    )


class PythonDetector:
    name = "python"

    def detect(self, context: DiscoveryContext) -> list[DiscoverySignal]:
        signals: list[DiscoverySignal] = []
        for path in context.files:
            basename = PurePosixPath(path).name
            if basename == "manage.py":
                root = _parent(path)
                signals.extend(
                    [
                        _signal(
                            self.name,
                            "component_anchor",
                            path,
                            root,
                            name="django_manage",
                            confidence=0.99,
                            anchor=True,
                        ),
                        _signal(
                            self.name,
                            "technology",
                            path,
                            root,
                            value="python",
                            confidence=0.99,
                        ),
                        _signal(
                            self.name,
                            "framework",
                            path,
                            root,
                            value="django",
                            confidence=0.99,
                        ),
                        _signal(
                            self.name,
                            "run_command",
                            path,
                            root,
                            command=["python", "manage.py", "runserver"],
                            confidence=0.78,
                        ),
                    ]
                )
                continue

            if basename == "pyproject.toml":
                signals.extend(self._detect_pyproject(context, path))
                continue

            if basename == "requirements.txt" or (
                basename.startswith("requirements-") and basename.endswith(".txt")
            ):
                signals.extend(self._detect_requirements(context, path))
        return signals

    def _detect_requirements(
        self, context: DiscoveryContext, path: str
    ) -> list[DiscoverySignal]:
        root = _parent(path)
        signals = [
            _signal(
                self.name,
                "manifest",
                path,
                root,
                name="python_requirements",
                confidence=0.92,
            ),
            _signal(
                self.name,
                "technology",
                path,
                root,
                value="python",
                confidence=0.92,
            ),
        ]
        try:
            packages = _requirements_packages(context.read_text(path))
        except (OSError, ValueError):
            return signals
        signals.extend(_python_framework_signals(self.name, path, root, packages))
        return signals

    def _detect_pyproject(self, context: DiscoveryContext, path: str) -> list[DiscoverySignal]:
        root = _parent(path)
        signals = [
            _signal(
                self.name,
                "manifest",
                path,
                root,
                name="pyproject",
                confidence=0.98,
            ),
            _signal(
                self.name,
                "technology",
                path,
                root,
                value="python",
                confidence=0.98,
            ),
        ]
        try:
            payload = tomllib.loads(context.read_text(path))
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            return signals

        project = payload.get("project") if isinstance(payload, dict) else None
        if isinstance(project, dict) and project.get("name"):
            signals.append(
                _signal(
                    self.name,
                    "component_anchor",
                    path,
                    root,
                    name="python_project",
                    confidence=0.88,
                    anchor=True,
                )
            )
        packages = _pyproject_packages(payload)
        signals.extend(_python_framework_signals(self.name, path, root, packages))
        return signals


class NodeDetector:
    name = "node"

    def detect(self, context: DiscoveryContext) -> list[DiscoverySignal]:
        signals: list[DiscoverySignal] = []
        for path in context.files:
            basename = PurePosixPath(path).name
            if basename == "package.json":
                signals.extend(self._detect_package_json(context, path))
            elif _is_vite_config(basename):
                root = _parent(path)
                signals.extend(
                    [
                        _signal(
                            self.name,
                            "component_anchor",
                            path,
                            root,
                            name="vite_config",
                            confidence=0.98,
                            anchor=True,
                        ),
                        _signal(
                            self.name,
                            "technology",
                            path,
                            root,
                            value="node",
                            confidence=0.95,
                        ),
                        _signal(
                            self.name,
                            "framework",
                            path,
                            root,
                            value="vite",
                            confidence=0.98,
                        ),
                    ]
                )
        return signals

    def _detect_package_json(
        self, context: DiscoveryContext, path: str
    ) -> list[DiscoverySignal]:
        root = _parent(path)
        signals = [
            _signal(
                self.name,
                "manifest",
                path,
                root,
                name="package_json",
                confidence=0.98,
            ),
            _signal(
                self.name,
                "technology",
                path,
                root,
                value="node",
                confidence=0.96,
            ),
        ]
        try:
            payload = json.loads(context.read_text(path))
        except (json.JSONDecodeError, OSError, ValueError):
            return signals
        if not isinstance(payload, dict):
            return signals

        packages = _node_packages(payload)
        frameworks = {
            framework
            for package, framework in _NODE_FRAMEWORK_PACKAGES.items()
            if package in packages
        }
        for framework in sorted(frameworks):
            signals.append(
                _signal(
                    self.name,
                    "framework",
                    path,
                    root,
                    value=framework,
                    confidence=0.94,
                )
            )

        scripts = payload.get("scripts", {})
        if not isinstance(scripts, dict):
            scripts = {}
        runnable = [name for name in ("start", "dev", "serve", "preview") if scripts.get(name)]
        if scripts.get("build") or runnable:
            signals.append(
                _signal(
                    self.name,
                    "component_anchor",
                    path,
                    root,
                    name="runnable_package",
                    confidence=0.9,
                    anchor=True,
                )
            )
        if scripts.get("build"):
            signals.append(
                _signal(
                    self.name,
                    "build_command",
                    path,
                    root,
                    command=["npm", "run", "build"],
                    confidence=0.88,
                )
            )
        for index, script_name in enumerate(runnable):
            signals.append(
                _signal(
                    self.name,
                    "run_command",
                    path,
                    root,
                    name=script_name,
                    command=["npm", "run", script_name],
                    confidence=max(0.72, 0.86 - index * 0.03),
                )
            )
        return signals


class DockerDetector:
    name = "docker"

    def detect(self, context: DiscoveryContext) -> list[DiscoverySignal]:
        signals: list[DiscoverySignal] = []
        for path in context.files:
            basename = PurePosixPath(path).name
            if not _is_dockerfile(basename):
                continue
            root = _parent(path)
            signals.extend(
                [
                    _signal(
                        self.name,
                        "component_anchor",
                        path,
                        root,
                        name="dockerfile",
                        confidence=0.97,
                        anchor=True,
                    ),
                    _signal(
                        self.name,
                        "dockerfile",
                        path,
                        root,
                        value=path,
                        confidence=0.99,
                    ),
                ]
            )
        return signals


class ComposeDetector:
    name = "compose"

    def detect(self, context: DiscoveryContext) -> list[DiscoverySignal]:
        signals: list[DiscoverySignal] = []
        for path in context.files:
            if PurePosixPath(path).name not in _COMPOSE_FILES:
                continue
            try:
                payload = yaml.safe_load(context.read_text(path)) or {}
            except (OSError, ValueError, yaml.YAMLError):
                continue
            services = payload.get("services") if isinstance(payload, dict) else None
            if not isinstance(services, dict):
                continue
            for service_name in sorted(services):
                raw_service = services[service_name]
                if not isinstance(raw_service, dict):
                    continue
                root = _compose_build_root(path, raw_service.get("build"))
                internal_port = _compose_internal_port(raw_service.get("ports"))
                signals.append(
                    _signal(
                        self.name,
                        "compose_service",
                        path,
                        root,
                        name="compose_service",
                        value=str(service_name),
                        confidence=0.98 if root is not None else 0.75,
                        anchor=root is not None,
                        metadata={
                            "compose_file": path,
                            **({"internal_port": str(internal_port)} if internal_port else {}),
                        },
                    )
                )
                if root is None:
                    continue
                signals.extend(
                    [
                        _signal(
                            self.name,
                            "build_command",
                            path,
                            root,
                            name="compose",
                            command=["docker", "compose", "-f", path, "build", str(service_name)],
                            confidence=0.96,
                        ),
                        _signal(
                            self.name,
                            "run_command",
                            path,
                            root,
                            name="compose",
                            command=["docker", "compose", "-f", path, "up", str(service_name)],
                            confidence=0.95,
                        ),
                    ]
                )
                for address in _compose_local_addresses(raw_service.get("ports")):
                    signals.append(
                        _signal(
                            self.name,
                            "local_address",
                            path,
                            root,
                            value=address,
                            confidence=0.82,
                        )
                    )
                health_path = _compose_health_path(raw_service.get("healthcheck"))
                if health_path is not None:
                    signals.append(
                        _signal(
                            self.name,
                            "healthcheck",
                            path,
                            root,
                            value=health_path,
                            confidence=0.96,
                        )
                    )
        return signals


def default_detectors() -> tuple[object, ...]:
    return (PythonDetector(), NodeDetector(), DockerDetector(), ComposeDetector())


def _requirements_packages(content: str) -> set[str]:
    packages: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        match = _REQUIREMENT_NAME.match(stripped)
        if match:
            packages.add(match.group(1).lower().replace("_", "-"))
    return packages


def _pyproject_packages(payload: dict) -> set[str]:
    packages: set[str] = set()
    project = payload.get("project")
    if isinstance(project, dict):
        dependencies = project.get("dependencies", [])
        if isinstance(dependencies, list):
            for dependency in dependencies:
                match = _REQUIREMENT_NAME.match(str(dependency))
                if match:
                    packages.add(match.group(1).lower().replace("_", "-"))
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for values in optional.values():
                if not isinstance(values, list):
                    continue
                for dependency in values:
                    match = _REQUIREMENT_NAME.match(str(dependency))
                    if match:
                        packages.add(match.group(1).lower().replace("_", "-"))

    tool = payload.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    dependencies = poetry.get("dependencies") if isinstance(poetry, dict) else None
    if isinstance(dependencies, dict):
        packages.update(str(name).lower().replace("_", "-") for name in dependencies)
    return packages


def _python_framework_signals(
    detector: str, path: str, root: str, packages: set[str]
) -> list[DiscoverySignal]:
    return [
        _signal(
            detector,
            "framework",
            path,
            root,
            value=framework,
            confidence=0.94,
        )
        for package, framework in sorted(_PYTHON_FRAMEWORK_PACKAGES.items())
        if package in packages
    ]


def _node_packages(payload: dict) -> set[str]:
    packages: set[str] = set()
    for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        values = payload.get(field)
        if isinstance(values, dict):
            packages.update(str(name).lower() for name in values)
    return packages


def _is_vite_config(basename: str) -> bool:
    return basename in {
        "vite.config.js",
        "vite.config.mjs",
        "vite.config.cjs",
        "vite.config.ts",
        "vite.config.mts",
        "vite.config.cts",
    }


def _is_dockerfile(basename: str) -> bool:
    lowered = basename.lower()
    return lowered == "dockerfile" or lowered.startswith("dockerfile.") or lowered.endswith(
        ".dockerfile"
    )


def _compose_build_root(compose_path: str, build: object) -> str | None:
    if isinstance(build, str):
        context_value = build
    elif isinstance(build, dict):
        context_value = build.get("context")
    else:
        return None
    if not isinstance(context_value, str) or not context_value.strip():
        return None

    context_path = PurePosixPath(context_value.replace("\\", "/"))
    if context_path.is_absolute() or ".." in context_path.parts:
        return None
    compose_parent = PurePosixPath(compose_path).parent
    combined = compose_parent / context_path
    parts = [part for part in combined.parts if part not in {"", "."}]
    return PurePosixPath(*parts).as_posix() if parts else "."


def _compose_local_addresses(ports: object) -> list[str]:
    if not isinstance(ports, list):
        return []
    addresses: set[str] = set()
    for port in ports:
        if isinstance(port, str):
            parts = port.split(":")
            if len(parts) == 2 and parts[0].isdigit():
                addresses.add(f"127.0.0.1:{parts[0]}")
            elif len(parts) >= 3 and parts[-2].isdigit():
                host = ":".join(parts[:-2]).strip("[]") or "127.0.0.1"
                if host in {"0.0.0.0", "::"}:
                    host = "127.0.0.1"
                if host in {"localhost", "127.0.0.1", "::1"}:
                    addresses.add(f"{host}:{parts[-2]}")
        elif isinstance(port, dict):
            published = port.get("published")
            host_ip = str(port.get("host_ip", "127.0.0.1"))
            if published is not None and str(published).isdigit():
                if host_ip in {"0.0.0.0", "::"}:
                    host_ip = "127.0.0.1"
                if host_ip in {"localhost", "127.0.0.1", "::1"}:
                    addresses.add(f"{host_ip}:{published}")
    return sorted(addresses)


def _compose_internal_port(ports: object) -> int | None:
    if not isinstance(ports, list):
        return None
    ports_found: set[int] = set()
    for port in ports:
        if isinstance(port, str):
            value = port.rsplit(":", 1)[-1].split("/", 1)[0]
            if value.isdecimal():
                ports_found.add(int(value))
        elif isinstance(port, dict):
            value = port.get("target")
            if isinstance(value, int) or (isinstance(value, str) and value.isdecimal()):
                ports_found.add(int(value))
    return next(iter(ports_found)) if len(ports_found) == 1 else None


def _compose_health_path(healthcheck: object) -> str | None:
    if not isinstance(healthcheck, dict):
        return None
    test = healthcheck.get("test")
    if isinstance(test, list):
        text = " ".join(str(item) for item in test)
    elif isinstance(test, str):
        text = test
    else:
        return None

    # Берем только URL path. Host/port позже заменит RuntimeServiceMap
    for raw_url in _HEALTH_URL.findall(text):
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.path.startswith("/")
            and not parsed.query
            and not parsed.fragment
            and ".." not in PurePosixPath(parsed.path).parts
        ):
            return parsed.path
    return None
