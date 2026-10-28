"""Standalone server and isolated ML worker dispatcher (PyInstaller entry point)."""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import runpy
import sys

from studio.runtime import configure_runtime, resource_root


def configure_stdio():
    # A GUI process on Windows can inherit no standard handles. Uvicorn's
    # formatter still calls stdout.isatty(), so provide real null streams.
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            stream = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, stream)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    # Required before argument dispatch: torch DataLoader may spawn frozen Python.
    multiprocessing.freeze_support()
    configure_stdio()
    configure_runtime()
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        kind, args = sys.argv[2], sys.argv[3:]
        if kind in {"train", "infer"}:
            if args[:1] != ["--repository"] or len(args) < 2:
                raise SystemExit("worker requires --repository")
            repository, args = Path(args[1]), args[2:]
            script = repository / "src" / f"{kind}.py"
            sys.path.insert(0, str(script.parent))
            sys.argv = [str(script), *args]
            runpy.run_path(str(script), run_name="__main__")
        elif kind in {"probe", "prepare", "infer-stream"}:
            module = {"probe": "studio.model_probe", "prepare": "studio.training_worker", "infer-stream": "studio.inference_worker"}[kind]
            sys.argv = [module, *args]
            runpy.run_module(module, run_name="__main__")
        else:
            raise SystemExit(f"Unknown worker: {kind}")
        return
    if sys.argv[1:] == ["--runtime-info"]:
        import json
        from studio.config import Settings
        settings = Settings.load()
        print(json.dumps({"resources": str(resource_root()), "data": str(settings.data),
                          "upstream": str(settings.upstream)}))
        return
    import threading
    import time
    import uvicorn
    from studio.api import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=int(os.environ.get("SONGCUT_PORT", "8765")), access_log=False))
    shutdown_file = os.environ.get("SONGCUT_SHUTDOWN_FILE")
    ready_file = os.environ.get("SONGCUT_READY_FILE")
    if shutdown_file or ready_file:
        def monitor_desktop():
            ready_written = False
            while not server.should_exit:
                if ready_file and server.started and not ready_written:
                    import json
                    Path(ready_file).write_text(json.dumps({"pid": os.getpid(), "port": server.config.port}), encoding="utf-8")
                    ready_written = True
                if shutdown_file and Path(shutdown_file).exists():
                    server.should_exit = True
                    return
                time.sleep(.05 if ready_file and not ready_written else .2)
        threading.Thread(target=monitor_desktop, daemon=True).start()
    try:
        server.run()
    finally:
        if ready_file:
            Path(ready_file).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
