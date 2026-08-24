import { describe, expect, it } from "vitest";
import { collectFolder } from "./useProjectImport";
import type { ImportedFileEntry } from "../api/types";

interface FakeFileInit {
  relativePath: string;
  content: string;
}

function fakeFiles(inits: FakeFileInit[]): File[] {
  return inits.map(({ relativePath, content }) => {
    const name = relativePath.split("/").pop() ?? "file";
    const file = new File([content], name);
    Object.defineProperty(file, "webkitRelativePath", { value: relativePath });
    return file;
  });
}

async function paths(files: File[]): Promise<string[]> {
  const { files: entries } = await collectFolder(files);
  return entries.map((entry: ImportedFileEntry) => entry.path).sort();
}

describe("collectFolder", () => {
  it("strips the root folder and keeps nested relative paths", async () => {
    const collected = await paths(
      fakeFiles([
        { relativePath: "MyApp/manage.py", content: "x" },
        { relativePath: "MyApp/backend/app/views.py", content: "y" },
        { relativePath: "MyApp/requirements.txt", content: "z" },
      ]),
    );
    expect(collected).toEqual([
      "backend/app/views.py",
      "manage.py",
      "requirements.txt",
    ]);
  });

  it("skips junk directories, build output and binary assets", async () => {
    const collected = await paths(
      fakeFiles([
        { relativePath: "app/src/main.py", content: "x" },
        { relativePath: "app/node_modules/react/index.js", content: "x" },
        { relativePath: "app/.git/config", content: "x" },
        { relativePath: "app/dist/bundle.js", content: "x" },
        { relativePath: "app/__pycache__/main.cpython.pyc", content: "x" },
        { relativePath: "app/logo.png", content: "x" },
        { relativePath: "app/.DS_Store", content: "x" },
      ]),
    );
    expect(collected).toEqual(["src/main.py"]);
  });

  it("rejects an empty selection with a helpful message", async () => {
    await expect(collectFolder([])).rejects.toThrow("Папка пуста");
  });

  it("produces base64 payloads the API can decode", async () => {
    const { files } = await collectFolder(
      fakeFiles([{ relativePath: "Proj/manage.py", content: "print('hi')" }]),
    );
    expect(files).toHaveLength(1);
    expect(atob(files[0].content_base64)).toBe("print('hi')");
  });
});
