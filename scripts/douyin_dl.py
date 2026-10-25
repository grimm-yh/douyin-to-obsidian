#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""douyin_dl.py — 从 fetch_douyin.py 抓到的详情 JSON 里下最高码率视频。

为什么单独一层：
  1) 播放地址必须带 `Referer: https://www.douyin.com/`，否则 403
  2) URL 含大量 `&` 和特殊字符，走 shell 会被转义破坏
  3) `video.play_addr` 是低码率默认档，必须从 `video.bit_rate[]` 按码率降序取
  4) 地址有时效（`cdn_url_expired`），抓完要尽快下

用法：
    python douyin_dl.py dy-<id>.api.json --out downloads
    python douyin_dl.py dy-<id>.api.json --meta-only     # 只看元数据不下文件
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
REFERER = "https://www.douyin.com/"


def log(m: str) -> None:
    print(f"[dl] {m}", flush=True)


def pick(api_json: str) -> dict:
    with open(api_json, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = [raw]

    best = None
    for entry in raw:
        ad = (entry.get("data") or {}).get("aweme_detail")
        if not ad:
            continue
        v = ad.get("video") or {}
        cand = []
        for b in (v.get("bit_rate") or []):
            pa = b.get("play_addr") or {}
            for u in (pa.get("url_list") or []):
                cand.append({"gear": b.get("gear_name"), "bit_rate": b.get("bit_rate") or 0,
                             "width": pa.get("width"), "height": pa.get("height"),
                             "size": pa.get("data_size"), "url": u})
                break
        if not cand:                      # 兜底：默认档
            pa = v.get("play_addr") or {}
            for u in (pa.get("url_list") or []):
                cand.append({"gear": "default", "bit_rate": 0, "url": u,
                             "width": pa.get("width"), "height": pa.get("height"),
                             "size": pa.get("data_size")})
                break
        if not cand:
            continue
        cand.sort(key=lambda x: x["bit_rate"], reverse=True)
        rec = {
            "aweme_id": ad.get("aweme_id"),
            "desc": (ad.get("desc") or "").strip(),
            "author": ((ad.get("author") or {}).get("nickname") or "").strip(),
            "create_time": ad.get("create_time"),
            "duration_ms": v.get("duration"),
            "best": cand[0],
            "all_gears": [(c["gear"], c["bit_rate"], c["width"], c["height"]) for c in cand],
        }
        if best is None or (rec["best"]["bit_rate"] or 0) > (best["best"]["bit_rate"] or 0):
            best = rec
    if not best:
        raise RuntimeError("JSON 里没有 aweme_detail / play_addr")
    return best


def safe_name(s: str, limit: int = 40) -> str:
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r'[\\/:*?"<>|#]', "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s[:limit] or "video"


def download(url: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-headers", f"Referer: {REFERER}\r\n",
           "-user_agent", UA,
           "-i", url, "-c", "copy", dest]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-600:])
    if not os.path.exists(dest) or os.path.getsize(dest) < 10240:
        raise RuntimeError("下载文件异常（过小或不存在）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("--out", default="downloads")
    ap.add_argument("--meta-only", action="store_true")
    args = ap.parse_args()

    info = pick(args.json)
    dur = (info.get("duration_ms") or 0) / 1000
    b = info["best"]
    log(f"aweme_id {info['aweme_id']} | {info['author']} | {dur:.1f}s")
    log(f"标题 {info['desc'][:70]}")
    log(f"档位 {len(info['all_gears'])} 个，选 {b['gear']} {b['width']}x{b['height']} "
        f"{b['bit_rate'] // 1000}kbps ~{(b['size'] or 0) / 1048576:.1f}MB")

    folder = os.path.join(args.out, f"douyin-{info['aweme_id']}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    with open(os.path.join(folder, "post_caption.txt"), "w", encoding="utf-8") as f:
        f.write(info["desc"])

    if args.meta_only:
        log(f"仅元数据 → {folder}")
        return 0

    dest = os.path.join(folder, "video.mp4")
    if os.path.exists(dest) and os.path.getsize(dest) > 10240:
        log(f"跳过（已存在）{dest}")
    else:
        log("下载中（必须带 Referer，否则 403）…")
        download(b["url"], dest)
    log(f"✓ {dest}  {os.path.getsize(dest) / 1048576:.1f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
