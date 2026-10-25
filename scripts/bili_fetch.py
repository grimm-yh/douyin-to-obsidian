#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili_fetch.py — B 站视频抓取。

与抖音的做法完全不同，这里不需要浏览器：
B 站有公开 API，`view` 和 `playurl` 都能直接调，**不需要 WBI 签名**（实测 2026-10）。

唯一硬要求是请求头带 `Referer: https://www.bilibili.com`，否则 CDN 返回 403。

能拿到什么取决于登录态：
    未登录 → 最高 480P（playurl 会静默降级）
    有 SESSDATA → 720P / 1080P（大会员还有 1080P+ / 4K）

支持的链接形式见 SUPPORTED_HINT。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://www.bilibili.com"
API = "https://api.bilibili.com"

SUPPORTED_HINT = """\
支持：
  https://www.bilibili.com/video/BV1xxxxxxxxx        （推荐，最稳）
  https://www.bilibili.com/video/av123456789
  https://b23.tv/xxxxxxx                             （手机分享短链）
  https://www.bilibili.com/video/BV1xxx?p=2          （多 P 视频，默认取第 1P）
不支持：
  番剧 / 直播 / 课程 / 动态 / 专栏 / 用户主页
"""

# 明确不支持的路径，先拦下来，避免给出莫名其妙的错误
_UNSUPPORTED = [
    (r"bilibili\.com/bangumi/", "番剧"),
    (r"bilibili\.com/live/", "直播"),
    (r"live\.bilibili\.com", "直播"),
    (r"bilibili\.com/cheese/", "课程"),
    (r"bilibili\.com/read/", "专栏"),
    (r"bilibili\.com/opus/", "动态"),
    (r"space\.bilibili\.com", "用户主页"),
    (r"bilibili\.com/space/", "用户主页"),
    (r"bilibili\.com/medialist/", "收藏夹"),
    (r"bilibili\.com/list/", "收藏夹"),
    (r"bilibili\.com/audio/", "音频"),
]


class BiliError(Exception):
    """带人话说明的异常，main 里统一打印。"""


# ------------------------------------------------------------------ 解析

def resolve_bvid(url: str) -> tuple[str, int]:
    """把各种形式的链接归一成 (bvid, 分P序号)。"""
    url = url.strip()

    for pat, kind in _UNSUPPORTED:
        if re.search(pat, url, re.I):
            raise BiliError(f"这是{kind}链接，暂不支持。\n{SUPPORTED_HINT}")

    # 短链：跟随跳转
    if re.search(r"b23\.tv/", url, re.I):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                url = r.geturl()
        except Exception as e:
            raise BiliError(f"短链解析失败（可能已失效或需要代理）：{url}\n  {type(e).__name__}: {e}")

    page = 1
    m = re.search(r"[?&]p=(\d+)", url)
    if m:
        page = max(1, int(m.group(1)))

    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if m:
        return m.group(1), page

    m = re.search(r"/video/av(\d+)", url, re.I)
    if m:
        return _avid_to_bvid(m.group(1)), page

    raise BiliError(f"从链接里找不到 BV 号或 av 号：{url}\n{SUPPORTED_HINT}")


def _avid_to_bvid(aid: str) -> str:
    """av 号转 BV 号。算法公开，避免额外请求一次接口。"""
    table = "fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF"
    s = [11, 10, 3, 8, 4, 6]
    x = int(aid) ^ 177451812
    x += 8728348608
    out = list("BV1  4 1 7  ")
    for i, pos in enumerate(s):
        out[pos] = table[x // (58 ** i) % 58]
    return "".join(out)


def _get(url: str, cookie: str | None = None, timeout: int = 30) -> dict:
    hdrs = {"User-Agent": UA, "Referer": REFERER, "Accept": "application/json"}
    if cookie:
        hdrs["Cookie"] = cookie
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_info(bvid: str, cookie: str | None = None) -> dict:
    """view 接口：标题、作者、时长、分P 列表。"""
    try:
        d = _get(f"{API}/x/web-interface/view?bvid={bvid}", cookie)
    except urllib.error.HTTPError as e:
        raise BiliError(f"接口请求失败 HTTP {e.code}，可能是网络或代理问题")
    except Exception as e:
        raise BiliError(f"接口请求失败：{type(e).__name__}: {e}")

    code = d.get("code")
    if code == -404:
        raise BiliError(f"视频不存在或已删除：{bvid}")
    if code == -403:
        raise BiliError(f"无权限访问（可能被设为私密）：{bvid}")
    if code == 62002:
        raise BiliError(f"该视频为「充电专属」，需要大会员/充电才能观看：{bvid}")
    if code != 0:
        raise BiliError(f"接口返回错误 code={code} message={d.get('message')}")
    return d["data"]


def fetch_playinfo(bvid: str, cid: int, cookie: str | None = None) -> dict:
    """playurl 接口：DASH 视频轨 + 音频轨。免 WBI 签名。"""
    q = urllib.parse.urlencode({
        "bvid": bvid, "cid": cid, "qn": 116, "fnval": 4048, "fourk": 1,
    })
    d = _get(f"{API}/x/player/playurl?{q}", cookie)
    if d.get("code") != 0:
        raise BiliError(f"取播放地址失败 code={d.get('code')} message={d.get('message')}")
    data = d["data"]
    if not (data.get("dash") or {}).get("video"):
        if data.get("durl"):
            raise BiliError("该视频只提供单流地址（可能是老视频或特殊稿件），暂不支持，"
                            "可换个视频或用别的工具下载。")
        raise BiliError("没有拿到播放地址，可能需要登录（设 BILI_SESSDATA）或该视频有区域限制")
    return data


def pick_quality(dash: dict) -> tuple[dict, dict, str]:
    """从 DASH 里挑最高画质的视频轨和最高码率音频轨。"""
    vids = dash["video"]
    # 同分辨率优先 avc（兼容性好），其次按码率
    best_v = max(vids, key=lambda v: (v.get("id", 0), v.get("width", 0) * v.get("height", 0),
                                      v.get("bandwidth", 0),
                                      1 if "avc" in v.get("codecs", "") else 0))
    auds = dash.get("audio") or []
    best_a = max(auds, key=lambda a: a.get("bandwidth", 0)) if auds else None
    label = f"{best_v.get('width')}x{best_v.get('height')} id={best_v.get('id')}"
    return best_v, best_a, label


# ------------------------------------------------------------------ 下载

def _download(url: str, dest: str, cookie: str | None = None) -> int:
    """流式下载。必须带 Referer，否则 403。"""
    hdrs = {"User-Agent": UA, "Referer": REFERER}
    if cookie:
        hdrs["Cookie"] = cookie
    req = urllib.request.Request(url, headers=hdrs)
    total = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    return total


def download(bvid: str, cid: int, folder: str, cookie: str | None = None,
             log=print) -> dict:
    """下视频轨 + 音频轨并合并成 mp4。返回元信息。"""
    play = fetch_playinfo(bvid, cid, cookie)
    v, a, label = pick_quality(play["dash"])

    os.makedirs(folder, exist_ok=True)
    vpath = os.path.join(folder, "_v.m4s")
    apath = os.path.join(folder, "_a.m4s")
    out = os.path.join(folder, "video.mp4")

    log(f"[bili] 画质 {label}  {round(v.get('bandwidth',0)/1000)}kbps")
    t0 = time.time()
    vb = _download(v["baseUrl"], vpath, cookie)
    log(f"[bili] 视频轨 {vb/1048576:.1f}MB / {time.time()-t0:.1f}s")

    if a:
        t1 = time.time()
        ab = _download(a["baseUrl"], apath, cookie)
        log(f"[bili] 音频轨 {ab/1048576:.1f}MB / {time.time()-t1:.1f}s")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", vpath, "-i", apath,
               "-c", "copy", "-movflags", "+faststart", out]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            raise BiliError("音视频合并失败：" + r.stderr.decode("utf-8", "replace")[-300:])
        for p in (vpath, apath):
            try:
                os.remove(p)
            except OSError:
                pass
    else:
        os.replace(vpath, out)

    size = os.path.getsize(out) / 1048576
    log(f"[bili] ✓ {out}  {size:.1f}MB")
    return {"quality": label, "bytes": os.path.getsize(out)}


# ------------------------------------------------------------------ 对外入口

def fetch(url: str, workdir: str, sessdata: str | None = None, page: int | None = None,
          log=print, fresh: bool = False) -> dict:
    """完整抓取：解析 → 取信息 → 下载 → 写 metadata.json。

    返回 {folder, metadata, bvid, quality}，metadata 字段与抖音侧对齐，
    这样 pipeline 的 S4 生成草稿时不用区分平台。

    fresh=False 且产物已存在时直接复用，不重新下载。
    """
    bvid, url_page = resolve_bvid(url)
    p = page or url_page
    cookie = f"SESSDATA={sessdata}" if sessdata else None

    info = fetch_info(bvid, cookie)
    pages = info.get("pages") or []
    if pages and p > len(pages):
        raise BiliError(f"该视频只有 {len(pages)} P，你指定的是第 {p} P")
    cid = pages[p - 1]["cid"] if pages else info["cid"]
    part_title = pages[p - 1].get("part", "") if pages and len(pages) > 1 else ""

    if len(pages) > 1:
        log(f"[bili] 多 P 视频（共 {len(pages)} P），只处理第 {p} P：{part_title}")

    folder = os.path.join(workdir, "downloads", f"bili-{bvid}" + (f"-p{p}" if len(pages) > 1 else ""))
    meta_path = os.path.join(folder, "metadata.json")
    video_path = os.path.join(folder, "video.mp4")

    if not fresh and os.path.exists(meta_path) and os.path.exists(video_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        log(f"[bili] 复用已有产物（加 --fresh 可重下）")
        return {"folder": folder, "metadata": meta, "bvid": bvid,
                "quality": meta.get("quality", "?"), "pages": len(pages), "cached": True}

    log(f"[bili] {info['title']}")
    log(f"[bili] UP 主 {info['owner']['name']} | 时长 {info['duration']}s")

    result = download(bvid, cid, folder, cookie, log=log)

    # 字段与抖音 metadata.json 对齐，S4 无需分支
    duration = (pages[p - 1]["duration"] if pages else info["duration"])
    meta = {
        "platform": "bilibili",
        "aweme_id": bvid,
        "desc": (info["title"] + (f" - {part_title}" if part_title else "")
                 + ("\n" + info["desc"] if info.get("desc") else "")),
        "author": info["owner"]["name"],
        "duration_ms": int(duration * 1000),
        "bvid": bvid,
        "cid": cid,
        "page": p,
        "pages": len(pages) or 1,
        "url": url,
        "pubdate": info.get("pubdate"),
        "quality": result["quality"],
    }
    with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open(os.path.join(folder, "post_caption.txt"), "w", encoding="utf-8") as f:
        f.write(info["title"] + "\n")

    return {"folder": folder, "metadata": meta, "bvid": bvid,
            "quality": result["quality"], "pages": len(pages)}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="B 站视频抓取（下载 + 元信息）")
    ap.add_argument("url")
    ap.add_argument("--workdir", default=os.getcwd())
    ap.add_argument("--sessdata", default=None, help="登录态，用于提升画质")
    ap.add_argument("--page", type=int, default=None, help="多 P 视频取第几 P")
    args = ap.parse_args()

    sess = args.sessdata or config.bili_sessdata()
    try:
        r = fetch(args.url, args.workdir, sess, args.page)
    except BiliError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(json.dumps({"folder": r["folder"], "quality": r["quality"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
