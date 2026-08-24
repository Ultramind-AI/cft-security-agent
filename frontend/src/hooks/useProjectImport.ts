import { useCallback, useState } from "react";
import { importProjectFiles, uploadProjectZip } from "../api/client";
import type { ApiProject, ImportedFileEntry } from "../api/types";

const SKIP_DIRECTORIES = new Set([
  ".git",
  ".hg",
  ".svn",
  "node_modules",
  ".venv",
  "venv",
  "env",
  "__pycache__",
  ".pytest_cache",
  ".ruff_cache",
  ".mypy_cache",
  ".idea",
  ".vscode",
  "dist",
  "build",
  ".next",
  ".nuxt",
  "coverage",
  ".tox",
  ".eggs",
  "*.egg-info",
]);

const SKIP_EXTENSIONS = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
  ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
  ".woff", ".woff2", ".ttf", ".eot", ".otf",
  ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".jar", ".war",
  ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
  ".exe", ".dll", ".so", ".dylib", ".bin", ".iso", ".img",
  ".sqlite", ".sqlite3", ".db", ".pyc", ".class",
]);

const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MAX_TOTAL_BYTES = 60 * 1024 * 1024;
const MAX_FILES = 4000;

export interface ImportProgress {
  phase: "idle" | "collecting" | "uploading" | "done" | "error";
  collected: number;
  message?: string;
}

function isSkippedPath(relativePath: string): boolean {
  const parts = relativePath.split("/");
  if (parts.some((part) => SKIP_DIRECTORIES.has(part))) return true;
  const base = parts[parts.length - 1];
  if (base === ".DS_Store" || base.startsWith("._")) return true;
  const dot = base.lastIndexOf(".");
  if (dot >= 0 && SKIP_EXTENSIONS.has(base.slice(dot).toLowerCase())) return true;
  return false;
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

async function fileEntry(file: File, path: string): Promise<ImportedFileEntry> {
  const buffer = await file.arrayBuffer();
  return { path, content_base64: toBase64(buffer) };
}

interface FolderSelection {
  name: string;
  files: ImportedFileEntry[];
}

/**
 * Collect a browser-selected folder into relative paths + base64 payloads.
 * The browser never sends absolute host paths — only paths relative to the
 * picked root, which the server re-validates inside its own staging area.
 */
export async function collectFolder(
  fileList: FileList | File[],
  onProgress?: (collected: number) => void,
): Promise<FolderSelection> {
  const all = Array.from(fileList);
  if (all.length === 0) throw new Error("Папка пуста");

  // webkitRelativePath looks like "MyProject/src/main.py"; strip the root.
  const first = all[0] as File & { webkitRelativePath?: string };
  const rootName =
    first.webkitRelativePath?.split("/")[0] ?? first.name.split("/")[0] ?? "project";

  const entries: ImportedFileEntry[] = [];
  let totalBytes = 0;

  for (const file of all) {
    const withPath = file as File & { webkitRelativePath?: string };
    const relative = (withPath.webkitRelativePath ?? file.name)
      .split("/")
      .slice(1)
      .join("/");
    if (!relative || isSkippedPath(relative)) continue;

    let stat: number;
    try {
      stat = file.size;
    } catch {
      continue;
    }
    if (stat > MAX_FILE_BYTES) continue;
    totalBytes += stat;
    if (totalBytes > MAX_TOTAL_BYTES) {
      throw new Error(
        `Проект слишком большой для загрузки через браузер (> ${MAX_TOTAL_BYTES / (1024 * 1024)} МБ после фильтрации). Используйте ZIP.`,
      );
    }
    entries.push(await fileEntry(file, relative));
    if (entries.length % 25 === 0) onProgress?.(entries.length);
    if (entries.length > MAX_FILES) {
      throw new Error(`Слишком много файлов (>${MAX_FILES}). Используйте ZIP.`);
    }
  }

  if (entries.length === 0) {
    throw new Error(
      "В папке не найдено подходящих файлов исходного кода. Импортируйте ZIP.",
    );
  }
  onProgress?.(entries.length);
  return { name: rootName, files: entries };
}

export function useProjectImport(onImported?: (project: ApiProject) => void) {
  const [progress, setProgress] = useState<ImportProgress>({ phase: "idle", collected: 0 });

  const importFolder = useCallback(
    async (fileList: FileList | File[]): Promise<ApiProject> => {
      setProgress({ phase: "collecting", collected: 0 });
      try {
        const selection = await collectFolder(fileList, (collected) =>
          setProgress({ phase: "collecting", collected }),
        );
        setProgress({ phase: "uploading", collected: selection.files.length });
        const project = await importProjectFiles(selection);
        setProgress({ phase: "done", collected: selection.files.length });
        onImported?.(project);
        setTimeout(() => setProgress({ phase: "idle", collected: 0 }), 2500);
        return project;
      } catch (error) {
        const message = error instanceof Error ? error.message : "Ошибка импорта папки";
        setProgress({ phase: "error", collected: 0, message });
        throw error;
      }
    },
    [onImported],
  );

  const importZip = useCallback(
    async (file: File): Promise<ApiProject> => {
      setProgress({ phase: "uploading", collected: 0 });
      try {
        const project = await uploadProjectZip(file, file.name);
        setProgress({ phase: "done", collected: 0 });
        onImported?.(project);
        setTimeout(() => setProgress({ phase: "idle", collected: 0 }), 2500);
        return project;
      } catch (error) {
        const message = error instanceof Error ? error.message : "Ошибка импорта ZIP";
        setProgress({ phase: "error", collected: 0, message });
        throw error;
      }
    },
    [onImported],
  );

  const reset = useCallback(() => setProgress({ phase: "idle", collected: 0 }), []);

  return { progress, importFolder, importZip, reset };
}
