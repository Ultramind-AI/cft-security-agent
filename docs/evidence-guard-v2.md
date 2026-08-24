# Защитный механизм Evidence v2

Защитный механизм Evidence — чистая детерминированная функция между
`collect_evidence` и `reevaluate`. Она анализирует Evidence и `ExecutionResult` текущего
`ActionProposal` и не вызывает LLM. Только Evidence может установить
терминальный статус `confirmed` или `rejected`; `ExecutionResult` используется
только для детерминированного выбора причины `inconclusive` либо следующей
итерации.

| Условие | Результат |
| --- | --- |
| Есть только надёжные (`high`/`medium`) терминальные Evidence с вердиктом `confirmed` | `confirmed` |
| Есть только надёжные терминальные Evidence с вердиктом `rejected` | `rejected` |
| Есть оба терминальных вердикта | `continue`, либо `inconclusive` при достижении лимита |
| Нет терминальных Evidence | `continue`, либо `inconclusive` при достижении лимита |
| Превышен лимит времени | `continue`, либо `inconclusive` с причиной `execution_timeout` при достижении лимита |
| Не удалась сборка target | `inconclusive` с причиной `build_failure` |
| Среда выполнения target не поддержана | `inconclusive` с причиной `unsupported_runtime` |
| Исполнение заблокировано изоляцией или политикой | `inconclusive` с причиной `isolation_or_policy_blocked` |

Статус `policy_blocked`, полученный от Validator до исполнения, остаётся
самостоятельным терминальным статусом и не преобразуется в `inconclusive`.

Порядок Evidence не влияет на решение: защитный механизм анализирует множество
терминальных вердиктов текущего действия, поэтому одинаковое состояние всегда приводит к
одинаковому результату.
