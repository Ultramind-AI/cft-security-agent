import { WarningCircle } from "@phosphor-icons/react";
import type { ChatMessage } from "../../api/types";
import { formatClock } from "../../lib/format";
import { MarkdownContent } from "./MarkdownContent";

export function AssistantMessage({ message }: { message: ChatMessage }) {
  const content = localizeHistoricalMessage(message.content);
  return (
    <article className={`conversation-message assistant-message ${message.kind}`}>
      <div className="message-label">
        <span>{message.kind === "status" ? "Пайплайн" : "Агент"}</span>
        <time>{formatClock(message.created_at)}</time>
      </div>
      <div className="message-content">
        {message.kind === "error" ? <WarningCircle size={17} /> : null}
        <MarkdownContent>{content}</MarkdownContent>
      </div>
    </article>
  );
}

function localizeHistoricalMessage(content: string): string {
  if (content.startsWith("Принял. Запускаю Discovery → SAST → sandbox-анализ")) {
    return (
      "Принял. Запускаю исследование проекта → статический анализ → проверку "
      + "в песочнице → сбор доказательств → итоговое решение. Буду показывать "
      + "реальные действия и доказательства по мере появления."
    );
  }
  return content
    .replaceAll("завершен с предупреждением (warn)", "завершен с предупреждениями")
    .replaceAll("Детерминированные факты (Evidence)", "Подтвержденные факты");
}
