"""Reproduce the pinned upstream checkout + reviewable patch; never overwrite a checkout."""
from pathlib import Path
import argparse
import sys
import hashlib
import json
import subprocess
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--skip-checkpoint", action="store_true", help="prepare upstream code without downloading model weights")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "packages/contracts/model-source.json").read_text(encoding="utf-8"))
repo = root / "upstream/AutoSlicer-v2"
if repo.exists():
    raise SystemExit(f"目录已存在，未覆盖：{repo}。检查后手动使用，或将其移开再执行。")
repo.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "clone", manifest["repository"], str(repo)], check=True)
subprocess.run(["git", "switch", "-c", "feat/apple-silicon", manifest["base_commit"]], cwd=repo, check=True)
subprocess.run(["git", "-c", "user.name=VTuber SongCut Build", "-c", "user.email=build@vtubersongcut.invalid", "am", str(root / "patches/0001-apple-silicon.patch")], cwd=repo, check=True)
if args.skip_checkpoint:
    print(f"上游代码与兼容补丁已准备：{repo}")
    sys.exit(0)
model = root / "var/models/best.pt"
model.parent.mkdir(parents=True, exist_ok=True)
if not model.exists():
    temporary = model.with_suffix(".download")
    urllib.request.urlretrieve(manifest["checkpoint_url"], temporary)
    if hashlib.sha256(temporary.read_bytes()).hexdigest() != manifest["checkpoint_sha256"]:
        temporary.unlink()
        raise SystemExit("模型校验失败，请重新核实下载来源")
    temporary.replace(model)
elif hashlib.sha256(model.read_bytes()).hexdigest() != manifest["checkpoint_sha256"]:
    raise SystemExit("本地已有模型与官方校验值不同，未覆盖")
print(f"上游与模型已准备：{repo}")
