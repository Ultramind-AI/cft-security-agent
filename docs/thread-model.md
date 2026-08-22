# Security Sandbox Threat Model

## Цель

Sandbox изолирует выполнение capability от host filesystem, CI secrets,
Docker socket, внешней сети и соседних проектов.

## Trust boundaries

- Agent не получает shell, произвольный URL или host path.
- Executor запускает только зарегистрированные capabilities.
- Sandbox получает только явные trusted inputs.
- Target repo доступен только read-only.

## Isolation

- Host filesystem: нет общего mount.
- Target: только `/target:ro`.
- Workspace: отдельный одноразовый tmpfs.
- Root filesystem: read-only.
- CI secrets/env: не наследуются; используется strict allowlist.
- Docker socket: не монтируется.
- Соседние проекты: не монтируются и недоступны.

## Network

- Default: `network=none`.
- `agent-runner ↔ target`: только через выделенную internal network.
- Внешняя сеть и произвольные destinations запрещены.

## Resource limits

- wall time;
- CPU;
- RAM;
- PIDs;
- размер файлов;
- размер stdout/stderr;
- число параллельных и повторных запусков.

## Privileges

- non-root user;
- `cap-drop=ALL`;
- `no-new-privileges`;
- immutable image digest.

## Cleanup

После каждого run должны удаляться container и все writable artifacts.
Ephemeral workspace не сохраняется.

## Enforceable invariants

1. Нет произвольного host mount.
2. Нет secrets inheritance.
3. Нет Docker socket.
4. Нет внешней сети по умолчанию.
5. Target доступен только read-only.
6. Writable state существует только в ephemeral workspace.
7. Превышение лимита принудительно завершает run.
8. При невозможности обеспечить security boundary — fail closed.
