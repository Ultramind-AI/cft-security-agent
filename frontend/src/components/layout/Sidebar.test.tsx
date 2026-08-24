import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

const session = {
  id: "chat-1",
  target_id: "demo",
  title: "Demo security chat",
  active_run_id: null,
  created_at: "2026-08-24T10:00:00Z",
  updated_at: "2026-08-24T10:00:00Z",
};

describe("Sidebar", () => {
  it("confirms before deleting a chat", async () => {
    const remove = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter>
        <Sidebar
          sessions={[session]}
          projects={[]}
          activeSessionId="chat-1"
          mobileOpen={false}
          onCloseMobile={() => undefined}
          onNewChat={() => undefined}
          onOpenProject={() => undefined}
          onOpenExisting={() => undefined}
          onDeleteChat={remove}
        />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Delete chat Demo security chat" }));
    expect(remove).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Delete chat" }));
    expect(remove).toHaveBeenCalledWith("chat-1");
  });
});
