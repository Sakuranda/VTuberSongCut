"""Provider boundaries. Adapters never import the HTTP layer or UI."""
from pathlib import Path
from typing import Protocol
from .domain import Clip, LyricCue, Project, SongCandidate


class SingingDetector(Protocol):
    def detect(self, source: Path, output: Path, duration_ms: int) -> list[Clip]: ...


class SongIdentifier(Protocol):
    def identify(self, sample: Path) -> list[SongCandidate]: ...


class LyricsProvider(Protocol):
    def search(self, song: SongCandidate) -> list[LyricCue]: ...


class LyricsAligner(Protocol):
    def align(self, audio: Path, text: str, offset_ms: int) -> list[LyricCue]: ...


class ClipRenderer(Protocol):
    def render(self, project: Project, clip: Clip, output: Path) -> list[Path]: ...


class CoverGenerator(Protocol):
    def generate(self, project: Project, clip: Clip, output: Path) -> Path: ...


class TitleGenerator(Protocol):
    def generate(self, clip: Clip) -> list[str]: ...


class Publisher(Protocol):
    def publish(self, files: list[Path], metadata: dict, idempotency_key: str) -> str: ...
