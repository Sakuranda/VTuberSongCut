"""Versioned domain records; all times use integer milliseconds in source time."""
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator


def uid() -> str:
    return uuid4().hex


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LyricWord(Record):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("歌词时间必须递增")
        return self


class LyricCue(LyricWord):
    words: list[LyricWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_words(self):
        previous = self.start_ms
        for word in self.words:
            if word.start_ms < previous or word.end_ms > self.end_ms:
                raise ValueError("逐字时间必须有序且位于歌词行内")
            previous = word.end_ms
        return self


class SubtitleStyle(Record):
    enabled: bool = False
    layout: Literal["single", "double", "ktv"] = "single"
    font_size: int = Field(default=42, ge=16, le=100)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight: str = Field(default="#FFCF70", pattern=r"^#[0-9A-Fa-f]{6}$")
    margin_bottom: int = Field(default=48, ge=0, le=400)


class SongCandidate(Record):
    title: str
    artist: str = ""
    provider: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_url: str | None = None
    evidence: str = ""


class ExportSettings(Record):
    video: bool = True
    audio: bool = True
    video_codec: Literal["h264", "hevc"] = "h264"
    resolution: Literal["source", "720p", "1080p", "2160p"] = "source"
    fps: Literal[24, 25, 30, 50, 60] | None = None
    rate_control: Literal["quality", "bitrate"] = "quality"
    crf: int = Field(default=20, ge=0, le=40)
    video_bitrate_kbps: int = Field(default=8000, ge=250, le=100000)
    audio_bitrate_kbps: Literal[64, 96, 128, 160, 192, 256, 320] = 192

    @model_validator(mode="after")
    def validate_outputs(self):
        if not self.video and not self.audio:
            raise ValueError("至少选择一种导出格式")
        return self


class Clip(Record):
    id: str = Field(default_factory=uid)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    title: str = "未命名歌曲"
    original_artist: str = ""
    performer: str = ""
    detection: Literal["manual", "autoslicer"] = "manual"
    candidates: list[SongCandidate] = Field(default_factory=list)
    lyrics: list[LyricCue] = Field(default_factory=list)
    subtitle: SubtitleStyle = Field(default_factory=SubtitleStyle)
    export_settings: ExportSettings = Field(default_factory=ExportSettings)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class Project(Record):
    schema_version: int = 1
    id: str = Field(default_factory=uid)
    revision: int = 0
    name: str
    source_name: str
    source_file: str
    duration_ms: int = Field(gt=0)
    has_video: bool = True
    source_fps: float | None = Field(default=None, gt=0)
    preview_file: str | None = None
    clips: list[Clip] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_clips(self):
        if len({c.id for c in self.clips}) != len(self.clips):
            raise ValueError("片段 ID 不能重复")
        if any(c.end_ms > self.duration_ms for c in self.clips):
            raise ValueError("片段不能超出录播长度")
        return self


class Job(Record):
    id: str = Field(default_factory=uid)
    project_id: str
    kind: str
    input_revision: int | None = None
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"] = "queued"
    message: str = "等待处理"
    artifacts: list[str] = Field(default_factory=list)
