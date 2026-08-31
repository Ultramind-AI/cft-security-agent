import { useEffect, useRef, useState } from "react";

export type SseState = "idle" | "live" | "done" | "error";

/**
 * Подписываемся на SSE stream одного payload type до терминального события `done`
 *
 * `onEvent` получает каждый payload и может писать в react-query cache или local state
 * Stream закрывается после `done` или unmount, transient сбои EventSource повторяет сам
 */
export function useSse<T>(
  url: string | null,
  eventName: string,
  onEvent: (payload: T) => void,
  onDone?: (payload: T) => void,
): SseState {
  const [state, setState] = useState<SseState>("idle");
  const handlers = useRef({ onEvent, onDone });
  handlers.current = { onEvent, onDone };

  useEffect(() => {
    if (!url) {
      setState("idle");
      return;
    }
    let closed = false;
    let source: EventSource | null = null;
    const parse = (raw: string): T | null => {
      try {
        return JSON.parse(raw) as T;
      } catch {
        return null;
      }
    };

    const connect = () => {
      source = new EventSource(url);
      source.addEventListener(eventName, (event) => {
        const payload = parse((event as MessageEvent<string>).data);
        if (payload !== null && !closed) {
          setState("live");
          handlers.current.onEvent(payload);
        }
      });
      source.addEventListener("done", (event) => {
        const payload = parse((event as MessageEvent<string>).data);
        if (payload !== null && !closed) handlers.current.onDone?.(payload);
        closed = true;
        source?.close();
        setState("done");
      });
      source.addEventListener("error", () => {
        // EventSource повторяет запрос сам, после `done` соединение не открываем
        if (!closed) setState((current) => (current === "live" ? "live" : "error"));
      });
    };

    setState("live");
    connect();
    return () => {
      closed = true;
      source?.close();
    };
  }, [url, eventName]);

  return state;
}
