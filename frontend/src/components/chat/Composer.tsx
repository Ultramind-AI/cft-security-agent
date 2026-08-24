import { useEffect, useRef, useState } from "react";
import { ArrowUp, CircleNotch, Paperclip } from "@phosphor-icons/react";
import type { ApiProject } from "../../api/types";

export interface ComposerProps {
  project: ApiProject | null;
  disabled?: boolean;
  sending?: boolean;
  onSend: (text: string) => Promise<unknown> | void;
  onOpenProject: () => void;
}

export function Composer({
  project,
  disabled = false,
  sending = false,
  onSend,
  onOpenProject,
}: ComposerProps) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [text]);

  const busy = sending || submitting;
  const canSend = Boolean(project) && !disabled && !busy && text.trim().length > 0;

  const submit = () => {
    const content = text.trim();
    if (!canSend || !content) return;
    setSubmitting(true);
    Promise.resolve(onSend(content))
      .then(() => setText(""))
      .catch(() => undefined)
      .finally(() => setSubmitting(false));
  };

  return (
    <div className="composer-dock">
      <div className="composer-card">
        <textarea
          ref={textareaRef}
          value={text}
          rows={1}
          aria-label="Сообщение Security Agent"
          placeholder={
            project
              ? "Ask Security Agent…"
              : "Open a project to start a security conversation"
          }
          disabled={!project || disabled}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />

        <div className="composer-footer">
          <button
            type="button"
            className="composer-project"
            onClick={onOpenProject}
            aria-label={project ? "Сменить проект" : "Открыть проект"}
          >
            <Paperclip size={16} weight="regular" />
            <span>{project?.name ?? "Open project"}</span>
          </button>

          <button
            type="button"
            className="composer-send"
            disabled={!canSend}
            onClick={submit}
            aria-label="Отправить"
          >
            {busy ? (
              <CircleNotch size={17} className="spin" />
            ) : (
              <ArrowUp size={17} weight="bold" />
            )}
          </button>
        </div>
      </div>
      <p className="composer-note">
        Enter to send · Shift+Enter for a new line · Project scope stays enforced by policy
      </p>
    </div>
  );
}
