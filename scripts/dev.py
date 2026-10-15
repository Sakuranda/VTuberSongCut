"""Start the built UI + API from any working directory."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
if not (root / "apps/web/dist/index.html").is_file():
    raise SystemExit("先在 apps/web 执行 npm install && npm run build")
raise SystemExit(subprocess.call([sys.executable, "-m", "studio.api"], cwd=root))
