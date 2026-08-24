import { FolderOpen } from "@phosphor-icons/react";
import { useMemo } from "react";
import { useOutletContext, useParams } from "react-router-dom";
import { Composer } from "../components/chat/Composer";
import { Conversation } from "../components/chat/Conversation";
import type { AppShellContext } from "../components/layout/AppLayout";
import { ProjectHeader } from "../components/layout/ProjectHeader";
import { useChat } from "../hooks/useChat";

export function ChatPage() {
  const { sessionId } = useParams();
  const shell = useOutletContext<AppShellContext>();
  const chat = useChat(sessionId);

  const activeProject = useMemo(() => {
    const targetId = chat.snapshot?.session.target_id;
    if (!targetId) return null;
    return shell.projects.find((project) => project.id === targetId) ?? null;
  }, [chat.snapshot?.session.target_id, shell.projects]);

  const sendError =
    chat.sendError instanceof Error ? chat.sendError.message : chat.sendError ? String(chat.sendError) : null;
  const loadError =
    chat.loadError instanceof Error ? chat.loadError.message : chat.loadError ? String(chat.loadError) : null;

  if (!sessionId) {
    return (
      <div className="chat-workspace empty-workspace">
        <ProjectHeader
          project={null}
          snapshot={null}
          streamState="idle"
          onOpenSidebar={shell.openSidebar}
        />
        <div className="empty-project-state">
          <FolderOpen size={30} weight="duotone" />
          <h1>Open a project</h1>
          <p>Select a folder, upload a ZIP, or continue with an imported project.</p>
          <button type="button" className="primary-action" onClick={shell.openProjectDialog}>
            Open project
          </button>
        </div>
        <Composer
          project={null}
          disabled
          onSend={() => undefined}
          onOpenProject={shell.openProjectDialog}
        />
      </div>
    );
  }

  return (
    <div className="chat-workspace">
      <ProjectHeader
        project={activeProject}
        snapshot={chat.snapshot}
        streamState={chat.streamState}
        onOpenSidebar={shell.openSidebar}
      />

      {chat.loading ? (
        <div className="workspace-state">Loading conversation…</div>
      ) : loadError ? (
        <div className="workspace-state error">
          <strong>Could not load this conversation</strong>
          <span>{loadError}</span>
          <button type="button" onClick={() => void chat.refetch()}>
            Retry
          </button>
        </div>
      ) : (
        <Conversation
          items={chat.timeline}
          runActive={chat.runActive}
          streamState={chat.streamState}
          transientError={sendError}
          onRetry={() => {
            chat.clearSendError();
            void chat.send("Перезапусти полный security-анализ");
          }}
          onSuggestedAction={(prompt) => {
            chat.clearSendError();
            void chat.send(prompt);
          }}
        />
      )}

      <Composer
        project={activeProject}
        disabled={!chat.snapshot || Boolean(loadError)}
        sending={chat.sending}
        onSend={(text) => {
          chat.clearSendError();
          return chat.send(text);
        }}
        onOpenProject={shell.openProjectDialog}
      />
    </div>
  );
}
