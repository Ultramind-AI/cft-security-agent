import { Cube, GitMerge, LockKey, ShieldCheck } from "@phosphor-icons/react";

const actions = [
  { icon: ShieldCheck, title: "Полный анализ безопасности", prompt: "Проведи полный анализ безопасности проекта" },
  { icon: LockKey, title: "Аутентификация и сессии", prompt: "Проверь аутентификацию, авторизацию и управление сессиями" },
  { icon: Cube, title: "Docker и среда выполнения", prompt: "Проанализируй Docker-конфигурацию и риски среды выполнения" },
  { icon: GitMerge, title: "Перед слиянием в CI", prompt: "Найди риски, которые должны блокировать слияние в CI" },
] as const;

export function SuggestedActions({ onSelect }: { onSelect: (prompt: string) => void }) {
  return (
    <section className="suggested-actions" aria-labelledby="suggested-actions-title">
      <span className="eyebrow">Начать анализ</span>
      <h1 id="suggested-actions-title">Что проверить?</h1>
      <p>Выберите направление или опишите свою задачу ниже.</p>
      <div className="suggested-action-list">
        {actions.map(({ icon: Icon, title, prompt }) => (
          <button type="button" key={title} onClick={() => onSelect(prompt)}>
            <Icon size={17} weight="duotone" />
            <span>{title}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
