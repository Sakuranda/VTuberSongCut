import { useEffect, useRef } from "react";
import { startVisiblePolling } from "./polling";

export function usePolling(
  action: (signal: AbortSignal) => Promise<boolean | void>,
  enabled: boolean,
  interval: number,
  key = "",
) {
  const latest = useRef(action);
  latest.current = action;
  useEffect(() => {
    if (!enabled) return;
    return startVisiblePolling((signal) => latest.current(signal), {
      interval,
    });
  }, [enabled, interval, key]);
}
