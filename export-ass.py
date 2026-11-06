import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import groupby
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen


TENCENT_API = "https://dm.video.qq.com/barrage"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "弹幕"
WIDTH = 1920
FONT_SIZE = 28
LANES = (18, 54, 90)
DURATION_MS = 7000
DEDUP_MS = 2000
PER_SECOND = 3
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Danmu,sans-serif,28,&H40FFFFFF,&H40FFFFFF,&H50000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass(frozen=True)
class Comment:
    time_ms: int
    text: str
    priority: float


def video_id(url):
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "v.qq.com":
        raise ValueError("请提供 v.qq.com 的单集播放链接。")
    vid = parse_qs(parsed.query).get("vid", [""])[0]
    if not vid:
        match = re.fullmatch(r"/x/(?:cover/[^/]+/|page/)([a-zA-Z0-9]+)\.html/?", parsed.path)
        vid = match[1] if match else ""
    if not re.fullmatch(r"[a-zA-Z0-9]+", vid):
        raise ValueError("链接里没有单集 VID，请打开具体一集后复制地址。")
    return vid


def request_json(path):
    for attempt in range(3):
        try:
            with urlopen(f"{TENCENT_API}/{path}", timeout=15) as response:
                return json.load(response)
        except (OSError, URLError, ValueError) as error:
            if attempt == 2:
                raise ValueError(f"腾讯弹幕请求失败（{path}）：{error}") from error


def time_arg(value):
    try:
        seconds = 0.0
        for part in value.split(":"):
            seconds = seconds * 60 + float(part)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError
        return round(seconds * 1000)
    except ValueError:
        raise argparse.ArgumentTypeError("请使用非负秒数或 MM:SS，例如 90 或 01:30。") from None


def comments_in_range(vid, start_ms, end_ms):
    index = request_json(f"base/{vid}").get("segment_index")
    if not isinstance(index, dict) or not index:
        raise ValueError("腾讯未返回可用的弹幕分段。")
    segments = []
    for segment in index.values():
        name = segment["segment_name"]
        match = re.fullmatch(r"t/v1/(\d+)/(\d+)", name)
        if not match:
            raise ValueError(f"无法识别腾讯弹幕分段：{name}")
        start, end = map(int, match.groups())
        if end > start_ms and (end_ms is None or start < end_ms):
            segments.append((start, name))

    def fetch(name):
        rows = request_json(f"segment/{vid}/{name}").get("barrage_list")
        if not isinstance(rows, list):
            raise ValueError(f"腾讯弹幕分段响应缺少列表：{name}")
        return rows

    print(f"正在读取腾讯弹幕，共 {len(segments)} 个分段。", flush=True)
    comments = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for rows in pool.map(fetch, [name for _, name in sorted(segments)]):
            for row in rows:
                timestamp = int(row["time_offset"])
                if timestamp < start_ms or (end_ms is not None and timestamp >= end_ms):
                    continue
                text = " ".join(row["content"].split())
                if not text:
                    continue
                likes = max(0, int(row.get("up_count") or 0))
                priority = (
                    2 * (float(row.get("content_score") or 0) == 10000)
                    + (int(row.get("show_weight") or 0) == 10)
                    + min(3, math.log10(1 + likes))
                )
                comments.append(Comment(timestamp, text, priority))
    return sorted(comments, key=lambda item: item.time_ms)


def rank(comment):
    return (-comment.priority, comment.time_ms, comment.text)


def deduplicate(comments):
    groups = {}
    unique = []
    for comment in comments:
        key = comment.text.casefold()
        start, best = groups.get(key, (comment.time_ms, comment))
        if comment.time_ms - start >= DEDUP_MS:
            unique.append(best)
            start, best = comment.time_ms, comment
        groups[key] = (start, min(best, comment, key=rank))
    unique.extend(best for _, best in groups.values())
    return sorted(unique, key=lambda item: item.time_ms)


def schedule(comments):
    free_at = [0] * len(LANES)
    limited_count = 0
    events = []
    for _, window in groupby(comments, key=lambda item: item.time_ms // 1000):
        selected = sorted(window, key=rank)[:PER_SECOND]
        limited_count += len(selected)
        for comment in selected:
            # Use the latest available lane to leave earlier slots for other candidates.
            free = [lane for lane, end in enumerate(free_at) if end <= comment.time_ms]
            if free:
                lane = max(free, key=free_at.__getitem__)
                free_at[lane] = comment.time_ms + DURATION_MS
                events.append((comment, LANES[lane]))
    return limited_count, sorted(events, key=lambda event: event[0].time_ms)


def ass_time(ms):
    seconds, cs = divmod(ms // 10, 100)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour}:{minute:02d}:{second:02d}.{cs:02d}"


def dialogue(comment, y, offset_ms, end_ms):
    # Prevent source text from becoming an ASS override or line-break command.
    text = comment.text.translate(str.maketrans({"\\": "＼", "{": "｛", "}": "｝"}))
    start = comment.time_ms
    end = start + DURATION_MS
    if end_ms is not None:
        end = min(end, end_ms)
    if (end - offset_ms) // 10 <= (start - offset_ms) // 10:
        return ""
    # A full em per character plus padding gives the text space to leave the frame.
    exit_x = -(len(text) + 2) * FONT_SIZE
    move = f"{{\\move({WIDTH},{y},{exit_x},{y},0,{DURATION_MS})}}"
    return (
        f"Dialogue: 0,{ass_time(start-offset_ms)},{ass_time(end-offset_ms)},"
        f"Danmu,,0,0,0,,{move}{text}\n"
    )


def export_ass(args):
    vid = video_id(args.url)
    start_ms = args.start
    end_ms = args.end
    if end_ms is not None and end_ms <= start_ms:
        raise ValueError("--end 必须晚于 --start。")
    suffix = f"-{start_ms}ms-{end_ms if end_ms is not None else 'end'}" if start_ms or end_ms else ""
    output = args.output or DEFAULT_OUTPUT_DIR / f"{vid}{suffix}.ass"
    output = output.expanduser().absolute()
    if output.suffix.lower() != ".ass":
        raise ValueError("输出文件名需要以 .ass 结尾。")
    if output.exists():
        raise ValueError(f"文件已存在，请用 -o 指定其他文件名。{output}")

    comments = comments_in_range(vid, start_ms, end_ms)
    unique = deduplicate(comments)
    limited, events = schedule(unique)
    offset_ms = start_ms if args.rebase else 0
    lines = [line for comment, y in events if (line := dialogue(comment, y, offset_ms, end_ms))]
    if not lines:
        raise ValueError("所选范围没有可导出的弹幕。")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent) as staged:
        staged.write(ASS_HEADER)
        staged.writelines(lines)
        staged.flush()
        os.link(staged.name, output)
    print(f"抓取 {len(comments):,} → 去重 {len(unique):,} → 限流 {limited:,} → 上屏 {len(lines):,} 条")
    print(output)


def main():
    parser = argparse.ArgumentParser(prog="danmu-ass", description="从腾讯视频导出顶部三行 ASS 弹幕。")
    parser.add_argument("url", help="腾讯视频单集播放链接")
    parser.add_argument("-o", "--output", type=Path, help="输出文件路径，默认保存在项目的「弹幕」目录")
    parser.add_argument("--start", type=time_arg, default=0, help="开始时间，秒数或 MM:SS")
    parser.add_argument("--end", type=time_arg, help="结束时间，秒数或 MM:SS")
    parser.add_argument("--rebase", action="store_true", help="将选段的起点对齐到 0 秒")
    args = parser.parse_args()
    try:
        export_ass(args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"danmu-ass: {error}\n")


if __name__ == "__main__":
    main()
