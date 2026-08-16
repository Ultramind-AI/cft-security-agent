# SAST v0.1

Первая SAST-интеграция проекта использует Semgrep Community Edition как локальный
статический анализатор. На этом этапе задача не состоит в автоматическом подтверждении
уязвимости. SAST только формирует кандидатов (`Finding`) для дальнейшего анализа,
CVSS/Context Priority и контролируемой проверки агентом.

## Поток

```text
SberLab source tree
→ Semgrep CE
→ raw semgrep.json
→ normalize_semgrep_payload()
→ Finding[]
→ findings.json + summary.json
→ ручной выбор 3-5 понятных findings
→ CVSS + Context Priority
→ один finding для end-to-end сценария
```

SAST читает исходный код. Для самого Semgrep-прогона SberLab не обязан быть запущен в
Docker. Запущенный target нужен позже, когда Validator/Executor выполняют разрешённую
проверку выбранной гипотезы.

## Установка

Из корня `cft-security-agent`:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,sast]"
```

Проверка:

```powershell
python -m pytest -q
python -m ruff check .
semgrep --version
```

## Первый прогон SberLab

Рекомендуемая структура каталогов:

```text
workspace/
├── cft-security-agent/
└── sberlab_hack/
```

Из `cft-security-agent`:

```powershell
python -m app.sast_scan --target ..\sberlab_hack
```

По умолчанию используется `--config auto`. Semgrep получает подходящие Community
Rules и анализирует локальный source tree. Для первого MVP это удобнее, чем заранее
фиксировать большой набор правил. Для воспроизводимого CI/CD позже следует закрепить
конкретный ruleset или локальные правила.

Артефакты создаются в `reports/sast/`:

```text
semgrep.json   # сырой JSON Semgrep
findings.json  # нормализованные Finding[]
summary.json   # количество по severity и компонентам
```

`reports/` считается локальным runtime-артефактом и не коммитится.

## Что делать после скана

Не считать каждый SAST finding подтверждённой уязвимостью. Сначала выбрать 3-5
понятных находок, посмотреть код вокруг них и определить компонент. После этого Лёха
может заполнить CVSS-метрики и Context Priority. Для end-to-end демо выбирается одна
безопасно проверяемая находка, которую затем обрабатывает агентный workflow.
