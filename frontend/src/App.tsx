import { NavLink, Route, Routes } from "react-router-dom";

import { ChatPage } from "./pages/ChatPage";
import { DashboardPage } from "./pages/DashboardPage";
import { RunPage } from "./pages/RunPage";

export function App() {
  return (
    <div className="app-frame">
      <header className="topbar">
        <NavLink className="brand" to="/">
          <span className="brand-mark">CFT</span>
          <span>
            <strong>Security Agent</strong>
            <small>chat-first evidence analysis</small>
          </span>
        </NavLink>
        <nav className="topbar-nav">
          <NavLink to="/">Chat</NavLink>
          <NavLink to="/dashboard">Runs</NavLink>
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<ChatPage />} />
        <Route path="/chats/:sessionId" element={<ChatPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="*" element={<ChatPage />} />
      </Routes>
    </div>
  );
}
