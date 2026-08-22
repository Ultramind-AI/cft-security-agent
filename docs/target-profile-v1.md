# TargetProfile v1

`TargetProfile` — единый контракт между конкретным репозиторием и core агента.

Core больше не должен определять проект через имена каталогов вроде `backend/frontend`, хранить реальные пути артефактов в agent model или зашивать id конкретного target.

## Что хранит профиль

- id и environment target;
- корень репозитория;
- сервисы и их типы;
- root каждого сервиса;
- dependency-файлы;
- Docker/Compose metadata;
- operator-owned build/run команды;
- healthcheck и разрешённые локальные адреса;
- trusted artifacts и их capability roles;
- ограничения target;
- optional metadata для capability-specific mappings.

## Service mapping

SAST больше не определяет сервис по первой папке.

```text
Semgrep path
→ TargetProfile.resolve_service(path)
→ Finding.service
```

Resolver нормализует `/` и `\\`, поэтому один профиль работает на macOS/Linux и Windows paths.

## Trusted artifacts

Agent получает artifact id только из `TargetProfile`. Validator отдельно проверяет, что `artifact_id` и `*_artifact_id` действительно зарегистрированы в текущем target. Executor повторно использует тот же trusted artifact registry.

## Policy

Global policy разрешает `registered` target вместо имени SberLab. Конкретный target считается разрешённым только когда его `TargetProfile` передан Validator.

Project-specific artifact ids больше не являются enum в global policy — их allowlist живёт в профиле target.

## Pipeline

Основной запуск:

```bash
python -m app.pipeline_run \
  --profile targets/sberlab.yaml \
  --target ../sberlab_hack
```

`--target` и `--architecture` остаются optional overrides. Остальные target-specific данные берутся из профиля.

## Что не входит в T02

`Project Discovery` не входит в эту задачу: сейчас профиль задаётся оператором. Автоматическое построение/дополнение профиля — T03.

Legacy runtime tools `check_sberlab_health` и `get_sberlab_public_projects` пока остаются для текущего MVP. Их замена на generic runtime actions относится к runtime/sandbox задачам.
