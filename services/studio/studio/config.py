import os
from dataclasses import dataclass
from pathlib import Path
from .runtime import configure_runtime, resource_root, data_root


@dataclass(frozen=True)
class Settings:
    root: Path
    data: Path
    upstream: Path
    checkpoint: Path
    device: str = "auto"
    model_python: str | None = None

    @classmethod
    def load(cls):
        configure_runtime()
        root = resource_root()
        data = data_root(root)
        return cls(root=root, data=data,
                   upstream=Path(os.environ.get("SONGCUT_UPSTREAM", root / "upstream/AutoSlicer-v2")).expanduser().resolve(),
                   checkpoint=Path(os.environ.get("SONGCUT_CHECKPOINT", data / "models/best.pt")).expanduser().resolve(),
                   device=os.environ.get("SONGCUT_DEVICE", "auto"),
                   model_python=os.environ.get("SONGCUT_MODEL_PYTHON"))
