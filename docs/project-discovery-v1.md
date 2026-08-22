# Project Discovery v1

T03 добавляет детерминированный слой между неизвестным репозиторием и `TargetProfile`.

```text
repository -> detectors -> DiscoverySignal[] -> resolver -> ProjectDiscoveryResult -> TargetProfileBuilder
```

## Инварианты

- имя репозитория не участвует в discovery;
- имя каталога не определяет роль компонента;
- язык или framework не означают `backend`/`frontend`;
- detector фиксирует наблюдаемый факт и источник, а не придумывает архитектуру;
- неоднозначный факт можно оставить неизвестным — guessing не является обязательным;
- `ProjectDiscoveryResult` описывает то, что нашли, а `TargetProfile` — что pipeline выбрал использовать;
- discovery читает файлы, но ничего из target не запускает.

## Первый набор detectors

Текущий набор нужен для проверки механизма на наших первых targets, а не задаёт список поддерживаемых архитектур:

- Python: `pyproject.toml`, `requirements*.txt`, `manage.py`;
- Node: `package.json`, `vite.config.*`;
- Docker: `Dockerfile*`;
- Compose: `compose*.yml/yaml`, `docker-compose*.yml/yaml`.

Новые detectors добавляются через `ProjectDetector` без изменения resolver или pipeline contracts.

## Component resolution

Сервис появляется из сильных anchor-сигналов: framework entrypoint/config, Dockerfile, runnable package или Compose build context. Manifest-only root используется как fallback, если под ним нет более конкретного component anchor.

Это специально не превращает `backend/` в backend и `frontend/` во frontend. Идентификатор может прийти из Compose service или из фактического component root, а type описывает найденные технологии (`django`, `express`, `react+vite` и т.п.), не архитектурную роль.

## Profile selection

`TargetProfileBuilder` выбирает детерминированные build/run/healthcheck candidates и формирует baseline `TargetProfile`. Если передан существующий профиль, его явные значения остаются override, а discovery заполняет отсутствующие поля.

Черновые target manifests внутри анализируемого проекта не являются источником discovery-фактов. Их можно использовать только как внешний expected result в тестах.
