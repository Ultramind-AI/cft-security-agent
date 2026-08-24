import { WarningCircle } from "@phosphor-icons/react";
import type { ChatMessage } from "../../api/types";
import { formatClock } from "../../lib/format";

export function AssistantMessage({ message }: { message: ChatMessage }) {
  return (
    <article className={`conversation-message assistant-message ${message.kind}`}>
      <div className="message-label">
        <span>Agent</span>
        <time>{formatClock(message.created_at)}</time>
      </div>
      <div className="message-content">
        {message.kind === "error" ? <WarningCircle size={17} /> : null}
        <p>{message.content}</p>
      </div>
    </article>
  );
}
