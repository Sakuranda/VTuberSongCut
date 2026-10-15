"""Local LRC ingest and ASS output. Never invent word-level timing."""
import re
from ..domain import LyricCue, Clip

STAMP = re.compile(r"\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str, offset_ms: int, duration_ms: int) -> list[LyricCue]:
    metadata_offset = re.search(r"\[offset:([+-]?\d+)\]", text, flags=re.I)
    offset_ms += int(metadata_offset[1]) if metadata_offset else 0
    lines = []
    for line in text.splitlines():
        stamps = list(STAMP.finditer(line))
        if not stamps:
            continue
        content = STAMP.sub("", line).strip()
        for match in stamps:
            seconds = int(match[2])
            if seconds >= 60:
                raise ValueError("LRC 秒数应小于 60")
            timestamp = (int(match[1])*60 + seconds)*1000 + int((match[3] or "0").ljust(3, "0"))
            lines.append((timestamp + offset_ms, content))
    grouped: dict[int, list[str]] = {}
    for timestamp, content in lines:
        grouped.setdefault(timestamp, []).append(content)
    lines = [(timestamp, "\n".join(dict.fromkeys(contents))) for timestamp, contents in sorted(grouped.items())]
    if not lines:
        raise ValueError("没有找到 LRC 时间标签，例如 [00:12.50]歌词")
    cues = []
    for i, (start, content) in enumerate(lines):
        end = lines[i+1][0] if i+1 < len(lines) else duration_ms
        start, end = max(0, start), min(end, duration_ms)
        if content and end > start:
            cues.append(LyricCue(start_ms=start, end_ms=end, text=content))
    return cues


def ass_time(ms: int) -> str:
    centiseconds = max(0, ms)//10
    seconds, cs = divmod(centiseconds, 100)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02}:{sec:02}.{cs:02}"


def escape_ass(text: str) -> str:
    # Strip override syntax from user-supplied lyrics and normalize line breaks.
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", " ")


def ass_color(hex_color: str) -> str:
    return "&H00" + hex_color[5:7] + hex_color[3:5] + hex_color[1:3]


def render_ass(clip: Clip, font_name: str = "Arial") -> str:
    font_name = font_name.replace(",", " ").replace("\n", " ").replace("\r", " ")
    style = clip.subtitle
    primary = ass_color(style.highlight if style.layout == "ktv" else style.color)
    secondary = ass_color(style.color)
    result = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 0", "",
              "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
              f"Style: Default,{font_name},{style.font_size},{primary},{secondary},&H00181B29,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,100,100,{style.margin_bottom},1", "",
              "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    cues = sorted(clip.lyrics, key=lambda c: c.start_ms)
    for i, cue in enumerate(cues):
        start, end = max(cue.start_ms, clip.start_ms), min(cue.end_ms, clip.end_ms)
        if end <= start:
            continue
        text = escape_ass(cue.text)
        if style.layout == "ktv":
            # Pair rows: top-left / bottom-right. Highlight only genuine aligned words.
            y = 1080 - style.margin_bottom - (style.font_size*1.5 if i % 2 == 0 else 0)
            alignment = 1 if i % 2 == 0 else 3
            x = 150 if i % 2 == 0 else 1770
            position = f"{{\\an{alignment}\\pos({x},{int(y)})}}"
            if cue.words:
                pieces = []
                cursor = start
                for word in cue.words:
                    a, b = max(start, word.start_ms), min(end, word.end_ms)
                    if word.end_ms <= start:
                        pieces.append("{\\kf0}" + escape_ass(word.text))
                    elif word.start_ms >= end:
                        pieces.append(f"{{\\c{ass_color(style.color)}}}" + escape_ass(word.text))
                    elif b > a:
                        if a > cursor:
                            pieces.append(f"{{\\k{(a-cursor)//10}}}\\h")
                        pieces.append(f"{{\\kf{max(1,(b-a)//10)}}}{escape_ass(word.text)}")
                        cursor = b
                text = "".join(pieces)
            else:
                text = f"{{\\c{ass_color(style.color)}}}" + text
            text = position + text
        elif style.layout == "double":
            next_line = escape_ass(cues[i+1].text) if i+1 < len(cues) and cues[i+1].start_ms < clip.end_ms else ""
            if next_line:
                text += "\\N{\\alpha&H70&}" + next_line
        if style.layout == "ktv" and i+1 < len(cues) and cues[i+1].start_ms < clip.end_ms:
            other_y = 1080 - style.margin_bottom - (style.font_size*1.5 if i % 2 else 0)
            other_align, other_x = (1, 150) if i % 2 else (3, 1770)
            upcoming = (f"{{\\an{other_align}\\pos({other_x},{int(other_y)})\\c{ass_color(style.color)}\\alpha&H70&}}"
                        + escape_ass(cues[i+1].text))
            result.append(f"Dialogue: 0,{ass_time(start-clip.start_ms)},{ass_time(end-clip.start_ms)},Default,,0,0,0,,{upcoming}")
        result.append(f"Dialogue: 0,{ass_time(start-clip.start_ms)},{ass_time(end-clip.start_ms)},Default,,0,0,0,,{text}")
    return "\n".join(result) + "\n"
