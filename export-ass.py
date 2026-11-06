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
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zlib


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


def tencent_id(parsed):
    vid = parse_qs(parsed.query).get("vid", [""])[0]
    if not vid:
        match = re.fullmatch(r"/x/(?:cover/[^/]+/|page/)([a-zA-Z0-9]+)\.html/?", parsed.path)
        vid = match[1] if match else ""
    if not re.fullmatch(r"[a-zA-Z0-9]+", vid):
        raise ValueError("链接里没有单集 VID，请打开具体一集后复制地址。")
    return vid


def request_data(url, referer=None, missing_ok=False):
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=15) as response:
                return response.read()
        except OSError as error:
            if isinstance(error, HTTPError) and error.code == 404 and missing_ok:
                return None
            if attempt == 2 or (isinstance(error, HTTPError) and 400 <= error.code < 500
                                and error.code != 429):
                raise ValueError(f"弹幕请求失败（{url}）：{error}") from error


def request_json(url, referer=None):
    return json.loads(request_data(url, referer))


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


def tencent_comments(vid, start_ms, end_ms):
    index = request_json(f"{TENCENT_API}/base/{vid}").get("segment_index")
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
        rows = request_json(f"{TENCENT_API}/segment/{vid}/{name}").get("barrage_list")
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


def bilibili_id(parsed):
    api = "https://api.bilibili.com"
    referer = "https://www.bilibili.com/"
    match = re.fullmatch(r"/video/(BV[a-zA-Z0-9]+|av[0-9]+)/?", parsed.path)
    if match:
        video = match[1]
        page = int(parse_qs(parsed.query).get("p", ["1"])[0])
        key, value = ("aid", video[2:]) if video.startswith("av") else ("bvid", video)
        response = request_json(f"{api}/x/player/pagelist?{key}={value}", referer)
        entries = response.get("data") or []
        entry = next((item for item in entries if item["page"] == page), None)
    else:
        match = re.fullmatch(r"/bangumi/play/ep([0-9]+)/?", parsed.path)
        if not match:
            raise ValueError("请提供 B 站视频播放页或番剧单集链接。")
        episode = int(match[1])
        response = request_json(f"{api}/pgc/view/web/season?ep_id={episode}", referer)
        entries = (response.get("result") or {}).get("episodes", [])
        entry = next((item for item in entries if item["id"] == episode), None)
    if not entry or not str(entry.get("cid", "")).isdigit():
        raise ValueError("B 站未返回对应分 P 或单集的 CID。")
    return str(entry["cid"])


def bilibili_comments(cid, start_ms, end_ms):
    print("正在读取 B 站公开弹幕 XML。", flush=True)
    data = request_data(f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}", "https://www.bilibili.com/")
    if not data.lstrip().startswith(b"<"):
        data = zlib.decompress(data, -zlib.MAX_WBITS)
    root = ET.fromstring(data)
    if root.tag != "i":
        raise ValueError("B 站弹幕接口未返回预期的 XML。")
    return xml_comments(
        ((row.attrib["p"].split(",")[0], row.text, "0") for row in root.findall("d")),
        start_ms, end_ms,
    )


def iqiyi_id(parsed):
    match = re.fullmatch(r"/v_([a-zA-Z0-9]+)\.html/?", parsed.path)
    if not match:
        raise ValueError("请提供爱奇艺 v_…html 单集播放链接。")
    response = request_json(f"https://pcw-api.iq.com/api/decode/{match[1]}?platformId=3&modeCode=intl&langCode=sg")
    tvid = str(response.get("data", ""))
    if not tvid.isdigit():
        raise ValueError("爱奇艺未返回该链接的 TVID。")
    return tvid


def iqiyi_comments(tvid, start_ms, end_ms):
    response = request_json(f"https://pcw-api.iqiyi.com/video/video/baseinfo/{tvid}")
    data = response.get("data")
    if not isinstance(data, dict) or not data.get("durationSec"):
        raise ValueError("爱奇艺未返回该集时长，无法确定弹幕分段。")
    duration_ms = round(float(data["durationSec"]) * 1000)
    stop_ms = min(duration_ms, end_ms) if end_ms is not None else duration_ms
    segment_ms = 300000
    segments = range(start_ms // segment_ms + 1, math.ceil(stop_ms / segment_ms) + 1)
    prefix = tvid.zfill(4)[-4:]
    base = f"https://cmts.iqiyi.com/bullet/{prefix[:2]}/{prefix[2:]}/{tvid}_300_"

    def fetch(segment):
        packed = request_data(f"{base}{segment}.z", "https://www.iqiyi.com/", missing_ok=True)
        if packed is None:
            return None
        root = parse_iqiyi_xml(zlib.decompress(packed))
        if root.findtext("code") != "A00000":
            raise ValueError(f"爱奇艺弹幕分段 {segment} 返回异常。")
        return xml_comments(
            ((row.findtext("showTime"), row.findtext("content"), row.findtext("likeCount"))
             for row in root.iter("bulletInfo")),
            start_ms, end_ms,
        )

    print(f"正在读取爱奇艺公开弹幕，共 {len(segments)} 个分段。", flush=True)
    comments = []
    missing = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(fetch, segments):
            if result is None:
                missing += 1
            else:
                comments.extend(result)
    if missing == len(segments) and missing:
        raise ValueError("所选范围没有可访问的爱奇艺 XML 弹幕分段，该剧集可能使用了其他弹幕格式。")
    if missing:
        print(f"爱奇艺有 {missing} 个分段未返回 XML，本次导出可能不完整。")
    return sorted(comments, key=lambda item: item.time_ms)


def parse_iqiyi_xml(data):
    def valid_reference(match):
        value = match[1]
        codepoint = int(value[1:], 16) if value.startswith(b"x") else int(value)
        valid = codepoint in (9, 10, 13) or (
            32 <= codepoint <= 0x10FFFF and not 0xD800 <= codepoint <= 0xDFFF
            and codepoint not in (0xFFFE, 0xFFFF)
        )
        return match[0] if valid else b""

    # Some comments contain numeric references to characters forbidden by XML.
    return ET.fromstring(re.sub(rb"&#(x[0-9a-fA-F]+|[0-9]+);", valid_reference, data))


def xml_comments(rows, start_ms, end_ms):
    comments = []
    for seconds, content, likes in rows:
        timestamp = round(float(seconds) * 1000)
        text = " ".join((content or "").split())
        if text and timestamp >= start_ms and (end_ms is None or timestamp < end_ms):
            priority = min(3, math.log10(1 + max(0, int(likes or 0))))
            comments.append(Comment(timestamp, text, priority))
    return sorted(comments, key=lambda item: item.time_ms)


def select_provider(url):
    parsed = urlsplit(url.strip())
    if parsed.scheme in {"http", "https"}:
        if parsed.hostname == "v.qq.com":
            return "", tencent_id(parsed), tencent_comments
        if parsed.hostname in {"www.bilibili.com", "m.bilibili.com", "bilibili.com"}:
            return "bilibili-", bilibili_id(parsed), bilibili_comments
        if parsed.hostname in {"www.iqiyi.com", "m.iqiyi.com", "iqiyi.com"}:
            return "iqiyi-", iqiyi_id(parsed), iqiyi_comments
    raise ValueError("请提供腾讯视频、B 站或爱奇艺的单集播放链接。")


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
    start_ms = args.start
    end_ms = args.end
    if end_ms is not None and end_ms <= start_ms:
        raise ValueError("--end 必须晚于 --start。")
    prefix, vid, fetch = select_provider(args.url)
    suffix = f"-{start_ms}ms-{end_ms if end_ms is not None else 'end'}" if start_ms or end_ms else ""
    output = args.output or DEFAULT_OUTPUT_DIR / f"{prefix}{vid}{suffix}.ass"
    output = output.expanduser().absolute()
    if output.suffix.lower() != ".ass":
        raise ValueError("输出文件名需要以 .ass 结尾。")
    if output.exists():
        raise ValueError(f"文件已存在，请用 -o 指定其他文件名。{output}")

    comments = fetch(vid, start_ms, end_ms)
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
    parser = argparse.ArgumentParser(prog="danmu-ass", description="从腾讯视频、B 站或爱奇艺导出顶部三行 ASS 弹幕。")
    parser.add_argument("url", help="单集视频播放链接")
    parser.add_argument("-o", "--output", type=Path, help="输出文件路径，默认保存在项目的「弹幕」目录")
    parser.add_argument("--start", type=time_arg, default=0, help="开始时间，秒数或 MM:SS")
    parser.add_argument("--end", type=time_arg, help="结束时间，秒数或 MM:SS")
    parser.add_argument("--rebase", action="store_true", help="将选段的起点对齐到 0 秒")
    args = parser.parse_args()
    try:
        export_ass(args)
    except (ValueError, OSError, KeyError, TypeError, ET.ParseError, zlib.error) as error:
        parser.exit(1, f"danmu-ass: {error}\n")


if __name__ == "__main__":
    main()
