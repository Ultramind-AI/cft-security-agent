import { Cube, GitMerge, LockKey, ShieldCheck } from "@phosphor-icons/react";

const actions = [
  { icon: ShieldCheck, title: "Full security analysis", prompt: "Проведи полный security-анализ проекта" },
  { icon: LockKey, title: "Authentication and sessions", prompt: "Проверь authentication, authorization и управление сессиями" },
  { icon: Cube, title: "Docker and runtime", prompt: "Проанализируй Docker-конфигурацию и runtime-риски" },
  { icon: GitMerge, title: "Before CI merge", prompt: "Найди риски, которые должны блокировать merge в CI" },
] as const;

export function SuggestedActions({ onSelect }: { onSelect: (prompt: string) => void }) {
  return (
    <section className="suggested-actions" aria-labelledby="suggested-actions-title">
      <span className="eyebrow">Start an analysis</span>
      <h1 id="suggested-actions-title">What should I check?</h1>
      <p>Choose a focus or describe your own task below.</p>
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
