import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  CheckCircle,
  CircleNotch,
  FileZip,
  FolderOpen,
  FolderSimple,
  X,
} from "@phosphor-icons/react";
import type { ApiProject } from "../../api/types";
import type { ImportProgress } from "../../hooks/useProjectImport";

type ProjectMode = "folder" | "zip" | "existing";

export interface OpenProjectDialogProps {
  open: boolean;
  projects: ApiProject[];
  progress: ImportProgress;
  error: string | null;
  onClose: () => void;
  onOpenFolder: (files: FileList) => void;
  onOpenZip: (file: File) => void;
  onOpenExisting: (targetId: string) => void;
}

export function OpenProjectDialog({
  open,
  projects,
  progress,
  error,
  onClose,
  onOpenFolder,
  onOpenZip,
  onOpenExisting,
}: OpenProjectDialogProps) {
  const [mode, setMode] = useState<ProjectMode>("folder");
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const busy = progress.phase === "collecting" || progress.phase === "uploading";

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose, open]);

  if (!open) return null;

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={() => !busy && onClose()}>
      <section
        className="project-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="open-project-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="dialog-header">
          <div>
            <p className="eyebrow">Контекст проекта</p>
            <h2 id="open-project-title">Открыть проект</h2>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            disabled={busy}
            aria-label="Закрыть"
          >
            <X size={18} />
          </button>
        </header>

        <div className="dialog-tabs" role="tablist" aria-label="Источник проекта">
          <Tab active={mode === "folder"} onClick={() => setMode("folder")}>
            Папка
          </Tab>
          <Tab active={mode === "zip"} onClick={() => setMode("zip")}>
            ZIP
          </Tab>
          <Tab active={mode === "existing"} onClick={() => setMode("existing")}>
            Импортированные
          </Tab>
        </div>

        <div className="dialog-body">
          {busy || progress.phase === "done" ? (
            <ImportState progress={progress} />
          ) : mode === "folder" ? (
            <ProjectSource
              icon={<FolderOpen size={28} weight="duotone" />}
              title="Открыть папку репозитория"
              description="Браузер передает отфильтрованное дерево относительных путей. Абсолютные пути вашего компьютера не отправляются."
              action="Выбрать папку"
              onClick={() => folderInputRef.current?.click()}
            />
          ) : mode === "zip" ? (
            <ProjectSource
              icon={<FileZip size={28} weight="duotone" />}
              title="Загрузить ZIP проекта"
              description="До начала исследования сервер отклоняет выход за границы архива, символические ссылки и слишком большие файлы."
              action="Выбрать ZIP"
              onClick={() => zipInputRef.current?.click()}
            />
          ) : (
            <ExistingProjects projects={projects} onOpen={onOpenExisting} />
          )}

          {(error || progress.phase === "error") && (
            <div className="inline-notice technical" role="alert">
              <strong>Не удалось импортировать проект</strong>
              <span>{error ?? progress.message ?? "Неизвестная ошибка импорта"}</span>
            </div>
          )}
        </div>

        <input
          ref={folderInputRef}
          type="file"
          multiple
          // @ts-expect-error webkitdirectory нужен как cross-browser fallback выбора папки
          webkitdirectory=""
          directory=""
          hidden
          onChange={(event) => {
            if (event.target.files?.length) onOpenFolder(event.target.files);
            event.target.value = "";
          }}
        />
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip,application/zip"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onOpenZip(file);
            event.target.value = "";
          }}
        />
      </section>
    </div>
  );
}

function Tab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={active ? "active" : ""}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function ProjectSource({
  icon,
  title,
  description,
  action,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  action: string;
  onClick: () => void;
}) {
  return (
    <div className="project-source">
      <div className="project-source-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      <button type="button" className="primary-action" onClick={onClick}>
        {action}
      </button>
    </div>
  );
}

function ExistingProjects({
  projects,
  onOpen,
}: {
  projects: ApiProject[];
  onOpen: (targetId: string) => void;
}) {
  if (projects.length === 0) {
    return <p className="dialog-empty">Импортированных проектов пока нет.</p>;
  }
  return (
    <div className="existing-projects">
      {projects.map((project) => (
        <button
          type="button"
          key={project.id}
          disabled={!project.repository_available}
          onClick={() => onOpen(project.id)}
        >
          <FolderSimple size={18} weight="duotone" />
          <span>
            <strong>{project.name}</strong>
            <small>
              {project.services.length > 0
                ? project.services.join(" · ")
                : project.environment}
            </small>
          </span>
          <span className={`project-availability ${project.repository_available ? "ready" : "missing"}`}>
            {project.repository_available ? "Готов" : "Репозиторий недоступен"}
          </span>
        </button>
      ))}
    </div>
  );
}

function ImportState({ progress }: { progress: ImportProgress }) {
  const done = progress.phase === "done";
  return (
    <div className="import-state" aria-live="polite">
      {done ? (
        <CheckCircle size={30} weight="duotone" />
      ) : (
        <CircleNotch size={30} className="spin" />
      )}
      <div>
        <strong>{done ? "Проект готов" : "Исследуем проект…"}</strong>
        <span>
          {progress.phase === "collecting"
            ? `Собрано исходных файлов: ${progress.collected}`
            : done
              ? "Открываем новый чат безопасности"
              : "Загружаем дерево файлов и создаем профиль цели"}
        </span>
      </div>
    </div>
  );
}
