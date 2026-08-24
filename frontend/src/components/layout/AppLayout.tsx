import { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  createChatSession,
  listChatSessions,
  listProjects,
} from "../../api/client";
import type { ApiProject, ChatSession } from "../../api/types";
import { useProjectImport } from "../../hooks/useProjectImport";
import { OpenProjectDialog } from "../project/OpenProjectDialog";
import { Sidebar } from "./Sidebar";

export interface AppShellContext {
  projects: ApiProject[];
  sessions: ChatSession[];
  openProjectDialog: () => void;
  openExistingProject: (targetId: string) => void;
  openSidebar: () => void;
}

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [shellError, setShellError] = useState<string | null>(null);

  const sessionsQuery = useQuery({
    queryKey: ["chat-sessions"],
    queryFn: () => listChatSessions(),
    refetchInterval: 8_000,
  });
  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });

  const createSession = useMutation({
    mutationFn: (targetId: string) => createChatSession(targetId),
    onSuccess: async (session) => {
      setShellError(null);
      setDialogOpen(false);
      setMobileOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      navigate(`/chats/${session.id}`);
    },
    onError: (error) => {
      setShellError(error instanceof Error ? error.message : "Could not open project chat");
    },
  });

  const handleImported = useCallback(
    (project: ApiProject) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      createSession.mutate(project.id);
    },
    [createSession, queryClient],
  );
  const projectImport = useProjectImport(handleImported);

  const openDialog = useCallback(() => {
    setShellError(null);
    projectImport.reset();
    setDialogOpen(true);
    setMobileOpen(false);
  }, [projectImport]);

  const openExistingProject = useCallback(
    (targetId: string) => {
      setShellError(null);
      createSession.mutate(targetId);
    },
    [createSession],
  );

  const activeSessionId = useMemo(() => {
    const match = location.pathname.match(/^\/chats\/([^/]+)/);
    return match?.[1];
  }, [location.pathname]);

  const context: AppShellContext = {
    projects: projectsQuery.data ?? [],
    sessions: sessionsQuery.data ?? [],
    openProjectDialog: openDialog,
    openExistingProject,
    openSidebar: () => setMobileOpen(true),
  };

  return (
    <div className="app-shell">
      <Sidebar
        sessions={context.sessions}
        projects={context.projects}
        activeSessionId={activeSessionId}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
        onNewChat={() => {
          navigate("/");
          openDialog();
        }}
        onOpenProject={openDialog}
        onOpenExisting={openExistingProject}
      />
      {mobileOpen ? (
        <button
          type="button"
          className="sidebar-scrim"
          aria-label="Закрыть меню"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}

      <main className="app-main">
        <Outlet context={context} />
      </main>

      <OpenProjectDialog
        open={dialogOpen}
        projects={context.projects}
        progress={projectImport.progress}
        error={shellError}
        onClose={() => setDialogOpen(false)}
        onOpenFolder={(files) => {
          projectImport.importFolder(files).catch(() => undefined);
        }}
        onOpenZip={(file) => {
          projectImport.importZip(file).catch(() => undefined);
        }}
        onOpenExisting={openExistingProject}
      />
    </div>
  );
}
