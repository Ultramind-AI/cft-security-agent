import type { ChatMessage } from "../../api/types";
import { formatClock } from "../../lib/format";

export function UserMessage({ message }: { message: ChatMessage }) {
  return (
    <article className="conversation-message user-message">
      <div className="message-label">
        <span>You</span>
        <time>{formatClock(message.created_at)}</time>
      </div>
      <div className="message-content">{message.content}</div>
    </article>
  );
}
