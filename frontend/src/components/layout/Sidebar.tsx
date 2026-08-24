import {
  Bug,
  ChatCircle,
  FolderOpen,
  FolderSimple,
  Plus,
  ShieldCheck,
  Trash,
  X,
} from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import type { ApiProject, ChatSession } from "../../api/types";
import { formatDateTime } from "../../lib/format";

export interface SidebarProps {
  sessions: ChatSession[];
  projects: ApiProject[];
  activeSessionId?: string;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  onNewChat: () => void;
  onOpenProject: () => void;
  onOpenExisting: (targetId: string) => void;
  onDeleteChat: (sessionId: string) => Promise<void>;
}

export function Sidebar({
  sessions,
  projects,
  activeSessionId,
  mobileOpen,
  onCloseMobile,
  onNewChat,
  onOpenProject,
  onOpenExisting,
  onDeleteChat,
}: SidebarProps) {
  const [pendingDelete, setPendingDelete] = useState<ChatSession | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await onDeleteChat(pendingDelete.id);
      setPendingDelete(null);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "Could not delete chat");
    } finally {
      setDeleting(false);
    }
  };
  return (
    <aside className={`sidebar ${mobileOpen ? "mobile-open" : ""}`}>
      <div className="sidebar-brand">
        <span className="brand-icon" aria-hidden="true">
          <ShieldCheck size={19} weight="duotone" />
        </span>
        <span>
          <strong>CFT Security</strong>
          <small>Agent workspace</small>
        </span>
        <button
          type="button"
          className="icon-button sidebar-close"
          onClick={onCloseMobile}
          aria-label="Закрыть меню"
        >
          <X size={18} />
        </button>
      </div>

      <div className="sidebar-actions">
        <button type="button" className="sidebar-primary" onClick={onNewChat}>
          <Plus size={16} weight="bold" />
          New chat
        </button>
        <button type="button" className="sidebar-action" onClick={onOpenProject}>
          <FolderOpen size={16} />
          Open project
        </button>
      </div>

      <nav className="sidebar-scroll" aria-label="Workspace navigation">
        <SidebarSection title="Projects">
          {projects.length === 0 ? (
            <p className="sidebar-empty">No projects</p>
          ) : (
            projects.map((project) => (
              <button
                type="button"
                className="sidebar-row project-row"
                key={project.id}
                disabled={!project.repository_available}
                onClick={() => onOpenExisting(project.id)}
              >
                <FolderSimple size={15} weight="duotone" />
                <span>{project.name}</span>
                <i className={project.repository_available ? "ready" : "missing"} />
              </button>
            ))
          )}
        </SidebarSection>

        <SidebarSection title="Recent chats">
          {sessions.length === 0 ? (
            <p className="sidebar-empty">No conversations yet</p>
          ) : (
            sessions.map((session) => (
              <div className="chat-row-shell" key={session.id}>
                <NavLink
                  to={`/chats/${session.id}`}
                  className={`sidebar-row chat-row ${activeSessionId === session.id ? "active" : ""}`}
                  onClick={onCloseMobile}
                >
                  <ChatCircle size={15} />
                  <span>
                    <strong>{session.title}</strong>
                    <small>{formatDateTime(session.updated_at)}</small>
                  </span>
                </NavLink>
                <button
                  type="button"
                  className="chat-delete-button"
                  aria-label={`Delete chat ${session.title}`}
                  onClick={() => {
                    setDeleteError(null);
                    setPendingDelete(session);
                  }}
                >
                  <Trash size={14} />
                </button>
              </div>
            ))
          )}
        </SidebarSection>
      </nav>

      <div className="sidebar-footer">
        <NavLink to="/debug/runs" onClick={onCloseMobile}>
          <Bug size={15} />
          Runs / Debug
        </NavLink>
      </div>

      {pendingDelete ? (
        <div className="confirm-backdrop" role="presentation">
          <div className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-chat-title">
            <span className="eyebrow">Conversation</span>
            <h2 id="delete-chat-title">Delete this chat?</h2>
            <p>“{pendingDelete.title}” will disappear from the sidebar. Analysis runs and Evidence stay available in Debug.</p>
            {deleteError ? <div className="confirm-error" role="alert">{deleteError}</div> : null}
            <div className="confirm-actions">
              <button type="button" onClick={() => setPendingDelete(null)} disabled={deleting}>Cancel</button>
              <button type="button" className="danger-action" onClick={() => void confirmDelete()} disabled={deleting}>
                {deleting ? "Deleting…" : "Delete chat"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </aside>
  );
}

function SidebarSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="sidebar-section">
      <h2>{title}</h2>
      <div className="sidebar-list">{children}</div>
    </section>
  );
}
