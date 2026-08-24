import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  chatEventsUrl,
  getChatSnapshot,
  sendChatMessage,
} from "../api/client";
import type { ChatSnapshot } from "../api/types";
import { buildConversationTimeline } from "../lib/timeline";
import { useSse } from "./useSse";

export function useChat(sessionId: string | undefined) {
  const queryClient = useQueryClient();
  const queryKey = ["chat", sessionId ?? "new"] as const;
  const snapshotQuery = useQuery({
    queryKey,
    queryFn: () => getChatSnapshot(sessionId!),
    enabled: Boolean(sessionId),
    refetchInterval: (query) => {
      const snapshot = query.state.data;
      return snapshot?.run?.status === "queued" || snapshot?.run?.status === "running"
        ? 4_000
        : false;
    },
  });

  const snapshot = snapshotQuery.data ?? null;
  const runActive =
    snapshot?.run?.status === "queued" || snapshot?.run?.status === "running";

  const streamState = useSse<ChatSnapshot>(
    sessionId && runActive ? chatEventsUrl(sessionId) : null,
    "snapshot",
    (payload) => queryClient.setQueryData(queryKey, payload),
    (payload) => {
      queryClient.setQueryData(queryKey, payload);
      void queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  );

  const sendMutation = useMutation({
    mutationFn: (content: string) => sendChatMessage(sessionId!, content),
    onSuccess: (payload) => {
      queryClient.setQueryData(queryKey, payload);
      void queryClient.invalidateQueries({ queryKey: ["chat-sessions"] });
    },
  });

  const timeline = useMemo(
    () => (snapshot ? buildConversationTimeline(snapshot) : []),
    [snapshot],
  );

  return {
    snapshot,
    timeline,
    runActive,
    streamState,
    loading: snapshotQuery.isLoading,
    loadError: snapshotQuery.error,
    sendError: sendMutation.error,
    sending: sendMutation.isPending,
    send: (content: string) => sendMutation.mutateAsync(content),
    clearSendError: () => sendMutation.reset(),
    refetch: snapshotQuery.refetch,
  };
}
