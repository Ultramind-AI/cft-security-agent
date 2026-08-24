import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppLayout } from "./components/layout/AppLayout";
import { ChatPage } from "./pages/ChatPage";
import { DebugRunsPage } from "./pages/DebugRunsPage";
import { RunPage } from "./pages/RunPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 5_000,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<ChatPage />} />
            <Route path="/chats/:sessionId" element={<ChatPage />} />
            <Route path="/debug/runs" element={<DebugRunsPage />} />
            <Route path="/dashboard" element={<Navigate to="/debug/runs" replace />} />
            <Route path="/runs/:runId" element={<RunPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
