import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DragEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";

import { api, chatEventsUrl } from "../api";
import { ChatAnalysisPanel } from "../components/ChatAnalysisPanel";
import type { ChatSnapshot } from "../types";

function messageClass(role: string, kind: string) {
  return `chat-message chat-message-${role} chat-message-${kind}`;
}

export function ChatPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [text, setText] = useState("Проведи полный security-анализ проекта");
  const [file, setFile] = useState<File | null>(null);
  const [targetId, setTargetId] = useState("");
  const [agentMode, setAgentMode] = useState<"llm" | "stub">("llm");
  const [maxIterations, setMaxIterations] = useState(5);
  const [dragging, setDragging] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const projects = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });
  const sessions = useQuery({
    queryKey: ["chat-sessions"],
    queryFn: api.listChatSessions,
    refetchInterval: 5000,
  });
  const snapshot = useQuery({
    queryKey: ["chat", sessionId],
    queryFn: () => api.getChatSnapshot(sessionId!),
    enabled: Boolean(sessionId),
  });

  useEffect(() => {
    if (!targetId && projects.data?.length) setTargetId(projects.data[0].id);
  }, [projects.data, targetId]);

  const streamedRunId = snapshot.data?.run?.id;

  useEffect(() => {
    if (!sessionId) return;
    const source = new EventSource(chatEventsUrl(sessionId));
    const update = (event: MessageEvent<string>) => {
      const next = JSON.parse(event.data) as ChatSnapshot;
      queryClient.setQueryData(["chat", sessionId], next);
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
    };
    const done = (event: MessageEvent<string>) => {
      update(event);
      source.close();
    };
    source.addEventListener("snapshot", update as EventListener);
    source.addEventListener("done", done as EventListener);
    return () => source.close();
  }, [queryClient, sessionId, streamedRunId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [snapshot.data?.messages.length, snapshot.data?.reports.length, snapshot.data?.run?.status]);

  const send = useMutation({
    mutationFn: async () => {
      const content = text.trim();
      if (!content) throw new Error("Напиши задачу агенту");

      let effectiveSessionId = sessionId;
      if (file) {
        const project = await api.uploadProject(file);
        const session = await api.createChatSession({
          target_id: project.id,
          title: project.name,
        });
        effectiveSessionId = session.id;
        await queryClient.invalidateQueries({ queryKey: ["projects"] });
      } else if (!effectiveSessionId) {
        if (!targetId) throw new Error("Выбери проект или приложи ZIP");
        const session = await api.createChatSession({ target_id: targetId });
        effectiveSessionId = session.id;
      }

      const next = await api.sendChatMessage(effectiveSessionId!, {
        content,
        agent_mode: agentMode,
        max_iterations: maxIterations,
      });
      return { sessionId: effectiveSessionId!, snapshot: next };
    },
    onSuccess: ({ sessionId: nextSessionId, snapshot: next }) => {
      queryClient.setQueryData(["chat", nextSessionId], next);
      queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      if (sessionId !== nextSessionId) navigate(`/chats/${nextSessionId}`);
      setFile(null);
      setText("");
    },
  });

  const activeProject = useMemo(() => {
    const id = snapshot.data?.session.target_id || targetId;
    return projects.data?.find((project) => project.id === id);
  }, [projects.data, snapshot.data?.session.target_id, targetId]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!send.isPending) send.mutate();
  }

  function acceptDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) setFile(dropped);
  }

  return (
    <main className="chat-shell">
      <aside className="chat-sidebar">
        <button className="new-chat-button" onClick={() => navigate("/")} type="button">
          + Новый чат
        </button>
        <div className="chat-sidebar-title">История</div>
        <div className="chat-session-list">
          {sessions.data?.map((session) => (
            <NavLink
              className={({ isActive }) => `chat-session-link${isActive ? " active" : ""}`}
              key={session.id}
              to={`/chats/${session.id}`}
            >
              <strong>{session.title}</strong>
              <small>{session.target_id}</small>
            </NavLink>
          ))}
        </div>
      </aside>

      <section className="chat-main">
        <header className="chat-head">
          <div>
            <span className="eyebrow">security chat</span>
            <h1>{snapshot.data?.session.title || "Новый анализ"}</h1>
          </div>
          <div className="chat-project-chip">
            {activeProject ? `${activeProject.name} · ${activeProject.services.join(", ") || "discovery"}` : "project not selected"}
          </div>
        </header>

        <div className="chat-feed">
          {!sessionId && (
            <div className="chat-welcome">
              <h2>Закинь проект и скажи, что проверить</h2>
              <p>
                ZIP проходит безопасное распаковывание и Discovery. Дальше тот же pipeline:
                SAST → sandbox → agent loop → Evidence → deterministic Gate.
              </p>
            </div>
          )}

          {snapshot.data?.messages.map((message) => (
            <article className={messageClass(message.role, message.kind)} key={message.id}>
              <div className="chat-role">{message.role === "user" ? "you" : "agent"}</div>
              <p>{message.content}</p>
            </article>
          ))}

          {snapshot.data && <ChatAnalysisPanel snapshot={snapshot.data} />}
          <div ref={bottomRef} />
        </div>

        <form className="chat-composer" onSubmit={submit}>
          <div
            className={`chat-dropzone${dragging ? " dragging" : ""}`}
            onDragEnter={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
            onDrop={acceptDrop}
          >
            <label className="file-button">
              ZIP проекта
              <input
                accept=".zip,application/zip"
                onChange={(event) => setFile(event.target.files?.[0] || null)}
                type="file"
              />
            </label>
            {file ? (
              <span className="file-name">{file.name}</span>
            ) : sessionId ? (
              <span className="drop-hint">Файл не нужен — чат уже привязан к проекту</span>
            ) : (
              <span className="drop-hint">или перетащи ZIP сюда</span>
            )}
          </div>

          {!sessionId && !file && (
            <select value={targetId} onChange={(event) => setTargetId(event.target.value)}>
              {projects.data?.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name || project.id}
                </option>
              ))}
            </select>
          )}

          <textarea
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Например: проведи полный анализ, особенно проверь auth и session security"
            rows={3}
            value={text}
          />

          <div className="chat-composer-footer">
            <div className="chat-options">
              <label>
                mode
                <select value={agentMode} onChange={(event) => setAgentMode(event.target.value as "llm" | "stub")}>
                  <option value="llm">LLM</option>
                  <option value="stub">stub</option>
                </select>
              </label>
              <label>
                max steps
                <input
                  max={8}
                  min={1}
                  onChange={(event) => setMaxIterations(Number(event.target.value))}
                  type="number"
                  value={maxIterations}
                />
              </label>
            </div>
            <button disabled={send.isPending || !text.trim()} type="submit">
              {send.isPending ? "Отправляю…" : "Отправить"}
            </button>
          </div>
          {send.error && <div className="chat-error-box">{send.error.message}</div>}
        </form>
      </section>
    </main>
  );
}
