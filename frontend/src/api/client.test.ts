import { afterEach, describe, expect, it, vi } from "vitest";
import { importProjectFiles } from "./client";

describe("API validation errors", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows structured FastAPI validation details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [
              {
                loc: ["body", "files", 0, "content_base64"],
                msg: "String should have at least 1 character",
                type: "string_too_short",
              },
            ],
          }),
          {
            status: 422,
            statusText: "Unprocessable Content",
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );

    await expect(
      importProjectFiles({
        name: "demo",
        files: [{ path: "package/__init__.py", content_base64: "" }],
      }),
    ).rejects.toEqual(
      expect.objectContaining({
        status: 422,
        message: "files.0.content_base64: String should have at least 1 character",
      }),
    );
  });
});
