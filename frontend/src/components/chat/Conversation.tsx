import { useEffect, useRef, useState } from "react";
import { ArrowDown, WifiSlash } from "@phosphor-icons/react";
import type { SseState } from "../../hooks/useSse";
import type { ConversationTimelineItem } from "../../lib/timeline";
import { AssistantMessage } from "./AssistantMessage";
import { EvidenceBlock } from "./EvidenceBlock";
import { FindingBlock } from "./FindingBlock";
import { GateBlock } from "./GateBlock";
import {
  AgentDecisionBlock,
  DiscoveryBlock,
  FindingProgressBlock,
  RunStartBlock,
  StageBlock,
  TechnicalErrorBlock,
} from "./TimelineEvent";
import { ToolCall } from "./ToolCall";
import { UserMessage } from "./UserMessage";
import { SuggestedActions } from "./SuggestedActions";

export function Conversation({
  items,
  runActive,
  streamState,
  transientError,
  onRetry,
  onSuggestedAction,
}: {
  items: ConversationTimelineItem[];
  runActive: boolean;
  streamState: SseState;
  transientError: string | null;
  onRetry: () => void;
  onSuggestedAction: (prompt: string) => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const mountedRef = useRef(false);
  const [unread, setUnread] = useState(false);

  useEffect(() => {
    if (followRef.current) {
      bottomRef.current?.scrollIntoView({
        behavior: mountedRef.current ? "smooth" : "auto",
        block: "end",
      });
      setUnread(false);
    } else if (mountedRef.current) {
      setUnread(true);
    }
    mountedRef.current = true;
  }, [items.length, transientError]);

  const jumpToLatest = () => {
    followRef.current = true;
    setUnread(false);
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  };

  return (
    <div className="conversation-shell">
      <div
        ref={viewportRef}
        className="conversation-scroll"
        onScroll={(event) => {
          const element = event.currentTarget;
          const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
          followRef.current = nearBottom;
          if (nearBottom) setUnread(false);
        }}
      >
        <div className="conversation-feed" aria-live="polite">
          {items.length === 0 && !runActive ? (
            <SuggestedActions onSelect={onSuggestedAction} />
          ) : null}
          {items.map((item) => (
            <div className={`timeline-entry ${item.kind}`} key={item.id}>
              <TimelineContent item={item} onRetry={onRetry} />
            </div>
          ))}

          {runActive && streamState === "error" ? (
            <div className="stream-status">
              <WifiSlash size={15} />
              Соединение прервано. Переподключаемся…
            </div>
          ) : null}

          {transientError ? (
            <div className="inline-notice technical" role="alert">
              <strong>Сообщение не отправлено</strong>
              <span>{transientError}</span>
            </div>
          ) : null}
          <div ref={bottomRef} className="conversation-bottom" />
        </div>
      </div>

      {unread ? (
        <button type="button" className="jump-latest" onClick={jumpToLatest}>
          <ArrowDown size={15} />
          Новые события
        </button>
      ) : null}
    </div>
  );
}

function TimelineContent({
  item,
  onRetry,
}: {
  item: ConversationTimelineItem;
  onRetry: () => void;
}) {
  switch (item.kind) {
    case "message":
      return item.message.role === "user" ? (
        <UserMessage message={item.message} />
      ) : (
        <AssistantMessage message={item.message} />
      );
    case "run_start":
      return <RunStartBlock run={item.run} />;
    case "stage":
      return <StageBlock stage={item.stage} />;
    case "discovery":
      return <DiscoveryBlock discovery={item.discovery} />;
    case "finding_progress":
      return <FindingProgressBlock event={item.event} />;
    case "decision":
      return <AgentDecisionBlock decision={item.decision} report={item.report} />;
    case "tool":
      return <ToolCall tool={item.tool} />;
    case "evidence":
      return <EvidenceBlock evidence={item.evidence} />;
    case "finding":
      return <FindingBlock report={item.report} />;
    case "technical_error":
      return <TechnicalErrorBlock run={item.run} onRetry={onRetry} />;
    case "gate":
      return <GateBlock gate={item.gate} reports={item.reports} run={item.run} />;
  }
}
