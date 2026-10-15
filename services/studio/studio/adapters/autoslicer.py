import json
import os
from pathlib import Path
import subprocess
import sys
from ..domain import Clip


class AutoSlicerDetector:
    def __init__(self, repository: Path, checkpoint: Path, device: str = "auto", python: str | None = None, ffmpeg: str = "ffmpeg"):
        self.repository, self.checkpoint, self.device = repository, checkpoint, device
        self.python = python or sys.executable
        self.ffmpeg = ffmpeg

    def detect(self, source: Path, output: Path, duration_ms: int) -> list[Clip]:
        if not (self.repository / "src/infer.py").is_file():
            raise RuntimeError("未安装 AutoSlicer，请运行项目安装步骤")
        if not self.checkpoint.is_file():
            raise RuntimeError("缺少声线模型，请先下载官方模型或配置模型路径")
        output.mkdir(parents=True, exist_ok=True)
        command = [self.python, str(self.repository / "src/infer.py"), "--input", str(source),
                   "--checkpoint", str(self.checkpoint), "--out-dir", str(output), "--device", self.device]
        # Separate interpreter keeps upstream's flat module names out of the application.
        environment = os.environ.copy()
        environment["AUTOSLICER_FFMPEG"] = self.ffmpeg
        environment["PYTHONUTF8"] = "1"
        with (output / "detector.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=6*3600, env=environment)
        if result.returncode:
            raise RuntimeError("检测失败，请查看任务目录中的 detector.log")
        data = json.loads((output / f"{source.stem}.segments.json").read_text(encoding="utf-8"))
        clips = []
        for segment in data["segments"]:
            start = max(0, round(segment["cut_start"] * 1000))
            end = min(duration_ms, round(segment["cut_end"] * 1000))
            if end > start:
                clips.append(Clip(start_ms=start, end_ms=end, detection="autoslicer", title=f"待识别歌曲 {len(clips)+1}"))
        return clips
