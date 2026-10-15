"""Standalone server and isolated ML worker dispatcher (PyInstaller entry point)."""
from __future__ import annotations

import multiprocessing
from pathlib import Path
import runpy
import sys

from studio.runtime import configure_runtime, resource_root


def main():
    # Required before argument dispatch: torch DataLoader may spawn frozen Python.
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
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
        elif kind in {"probe", "prepare"}:
            module = "studio.model_probe" if kind == "probe" else "studio.training_worker"
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
    import os
    import threading
    import time
    import uvicorn
    from studio.api import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=int(os.environ.get("SONGCUT_PORT", "8765")), access_log=False))
    if shutdown_file := os.environ.get("SONGCUT_SHUTDOWN_FILE"):
        def await_shutdown():
            while not server.should_exit:
                if Path(shutdown_file).exists():
                    server.should_exit = True
                    return
                time.sleep(.2)
        threading.Thread(target=await_shutdown, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
