/** One request at a time; background tabs stop polling and abort stale reads. */
export function startVisiblePolling(
  task: (signal: AbortSignal) => Promise<boolean | void>,
  options: {
    interval: number;
    visibility?: EventTarget & { readonly hidden: boolean };
    schedule?: typeof setTimeout;
    cancel?: typeof clearTimeout;
  },
) {
  const visibility = options.visibility ?? document;
  const schedule = options.schedule ?? setTimeout;
  const cancel = options.cancel ?? clearTimeout;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let stopped = false;
  let pending = false;
  let restart = false;
  async function run() {
    if (stopped || visibility.hidden || pending) return;
    pending = true;
    controller = new AbortController();
    let again = true;
    try {
      again = (await task(controller.signal)) !== false;
    } catch {
      /* The caller displays failures; retry only while visible. */
    } finally {
      pending = false;
      controller = undefined;
      if (!stopped && !visibility.hidden) {
        if (restart) {
          restart = false;
          void run();
        } else if (again) timer = schedule(() => void run(), options.interval);
      }
    }
  }
  function visibilityChanged() {
    cancel(timer);
    if (visibility.hidden) controller?.abort();
    else if (pending) restart = true;
    else void run();
  }
  visibility.addEventListener("visibilitychange", visibilityChanged);
  void run();
  return () => {
    stopped = true;
    cancel(timer);
    controller?.abort();
    visibility.removeEventListener("visibilitychange", visibilityChanged);
  };
}
