import { useEffect, useRef, useState } from "react";

export type SseState = "idle" | "live" | "done" | "error";

/**
 * Subscribe to a server-sent event stream that repeats one payload type and
 * finishes with a terminal `done` event carrying the final payload.
 *
 * `onEvent` receives every payload; it may write into a react-query cache or
 * local state. The stream is closed after `done` or when the component
 * unmounts. EventSource reconnects automatically on transient failures.
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
        // EventSource retries automatically; after `done` we stay closed.
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
