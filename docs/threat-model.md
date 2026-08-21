# Модель угроз Security Sandbox

Sandbox изолирует выполнение capability от файловой системы хоста, секретов CI,
Docker socket, внешних сетей и соседних Compose-проектов.

## Границы доверия

- Агент никогда не передаёт shell, пути хоста, произвольные URL или аргументы Docker.
- Executor принимает только зарегистрированные capabilities и метаданные, принадлежащие цели.
- Репозитории целей монтируются в контейнеры capability только для чтения.
- Образы Docker sandbox — неизменяемые digest references; они используют non-root user,
  `cap-drop=ALL`, `no-new-privileges`, read-only root filesystem и ограниченный tmpfs.

## Сеть и ресурсы

Сеть по умолчанию — `none`. Capability может обращаться к цели только через
настроенную internal network. CPU, память, PIDs, wall time, объём output и размер workspace
ограничены `SandboxPolicy`; секреты CI не наследуются.

## Compose-сессии

`SandboxSession` принимает только доверенный `TargetDefinition`. Он отклоняет внешние
Compose networks и volumes, а также host bind mounts. У каждой сессии есть собственное
имя Compose project, а значит — собственные контейнеры, сети и volumes. Очистка использует
это имя проекта и только после ошибки `compose down` — точную метку
`com.docker.compose.project`. Глобальные команды Docker prune никогда не используются.
Отсутствие оставшихся ресурсов принимается только после успешных запросов Docker для
контейнеров, сетей и volumes.

Если необходимая Docker isolation не может быть установлена, выполнение завершается
по принципу fail-closed, а не переходит к более слабой границе безопасности.
