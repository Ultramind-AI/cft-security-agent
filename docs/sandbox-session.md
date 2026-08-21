# Сессия Sandbox

`executor.sandbox_session.SandboxSession` управляет одной локальной доверенной
целью Docker Compose: от подготовки до очистки. Реализация намеренно небольшая
и предназначена для локальной разработки и CI, а не для оркестрации целей production.

```python
from executor.sandbox_session import SandboxSession

with SandboxSession(target) as session:
    state = session.collect_state()
    assert state["ready"]
```

Состояния: `created`, `preparing`, `starting`, `ready`, `tearing_down`,
`closed`, `failed` и `timed_out`.

Подготовка проверяет каталог цели и настроенный Compose-файл, запускает
`docker compose config` и отклоняет внешние сети, внешние volumes и host bind mounts.
При запуске используется уникальный проект `cft-sandbox-<session-id>` и выполняется
ожидание фиксированного endpoint `/health/`, принадлежащего цели. У `compose up --build`
есть отдельный ограниченный `startup_timeout` (по умолчанию 300 секунд); для коротких
команд конфигурации, диагностики, очистки и проверки меток сохраняется `command_timeout`.

При штатном завершении, ошибке запуска, timeout healthcheck или исключении внутри
контекстного менеджера очистка выполняет `docker compose down --volumes --remove-orphans`
в блоке `finally`. Если эта команда завершилась ошибкой, резервная очистка ограничена
ресурсами с точной меткой Compose project. Затем сессия проверяет контейнеры, сети и
volumes с этой меткой и сообщает об оставшихся именах. Ошибка любого запроса Docker
также является ошибкой очистки: она никогда не интерпретируется как пустой результат.

Unit-тесты запускаются командой `python -m pytest tests/test_sandbox_session.py -q`.
Интеграционный Docker/SberLab-тест читает `SBERLAB_TARGET_PATH`; он пропускается только
когда недоступен Docker daemon или локальный checkout цели.

Граница безопасности включает уникальный проект, локальные для сессии Docker network
и volumes, отсутствие Docker socket в контейнерах capability, read-only mounts цели,
ограничения ресурсов из policy и отсутствие наследуемых секретов CI.
