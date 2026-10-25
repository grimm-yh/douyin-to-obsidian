#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr_frames.py — 抓取视频画面里"烧死的文字"。

抖音口播视频经常把关键信息（配方克数、参数表、步骤清单）做成半透明卡片贴
在画面上，而**作者并不念出来**。纯 ASR 拿不到这类信息，只能靠视觉模型读。

做法：
    1fps 抽帧（缩到 720 宽，够认字又省 token）
    → 每 N 帧打包成一次请求（DeepSeek 单请求上限 600 张图）
    → 模型自己挑出"有叠加文字"的帧并抄写内容
    → 相邻秒同内容合并

实测（245s / 1080x1920 视频）：245 帧 / 9 批 / 约 148K 输入 tokens / 约 25 秒。

用法：
    python ocr_frames.py <video.mp4>                  # 全片扫描，写 overlay_text.json
    python ocr_frames.py <video.mp4> --from 25 --to 45  # 只扫一段
    python ocr_frames.py <video.mp4> --batch 40 --workers 4
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

API = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"      # V4.1 Flash，原生多模态
FRAME_WIDTH = 720

PROMPT = """下面是同一段竖屏短视频按时间顺序抽取的 {n} 帧。
第 1 张对应视频第 {start} 秒，第 2 张第 {start2} 秒，依此类推，每张间隔 1 秒。

任务：找出其中哪些帧的画面上存在【叠加在视频画面之上的信息卡或文字贴纸】
（半透明的文字卡片、清单、表格、大字标语、字幕贴纸都算）。
**不包括**画面最底部那条随说话滚动的对白字幕。

对每一张命中的帧，逐字抄出卡片上的全部文字，保留数字与单位，不要改写、不要总结。
同一段卡片连续出现多帧时，只给出最早的那一帧。

只输出 JSON 数组，不要解释、不要 markdown 代码块：
[{{"second": 该帧对应的视频秒数, "text": "卡片原文，用 \\n 分行"}}]
如果没有任何帧存在叠加文字，输出 []。"""


def extract_frames(video: str, out_dir: str, start: float | None, end: float | None,
                   fps: int = 1) -> int:
    """抽帧到 out_dir，文件名保证按时间排序。返回帧数。"""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-t", str(end - (start or 0))]
    cmd += ["-i", video, "-vf", f"fps={fps},scale={FRAME_WIDTH}:-1",
            os.path.join(out_dir, "%05d.jpg")]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 抽帧失败：" + r.stderr.decode("utf-8", "replace")[-400:])
    return len([f for f in os.listdir(out_dir) if f.endswith(".jpg")])


def call_batch(files: list[str], seq_dir: str, start_sec: int, api_key: str,
               retries: int = 2) -> list[dict]:
    """一次请求送一批帧，返回命中的 [{second, text}]。"""
    n = len(files)
    prompt = PROMPT.format(n=n, start=start_sec, start2=start_sec + 1)

    content = [{"type": "text", "text": prompt}]
    for f in files:
        with open(os.path.join(seq_dir, f), "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}",
                                      "detail": "original"}})

    payload = {"model": MODEL,
               "messages": [{"role": "user", "content": content}],
               "temperature": 0}
    raw = json.dumps(payload).encode("utf-8")

    last_err = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            API, data=raw,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.load(r)
            txt = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            return {"blocks": parse_json(txt), "usage": usage, "raw": txt,
                    "start": start_sec, "n": n}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            last_err = f"HTTP {e.code}: {body[:300]}"
            if e.code in (400, 401, 402, 403):
                break                      # key 或参数问题，重试没意义
        except Exception as e:             # 网络抖动、超时
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    print(f"  [ocr] ✗ 批次 {start_sec}s 失败：{last_err}", flush=True)
    return {"blocks": [], "usage": {}, "raw": "", "start": start_sec, "n": n, "error": last_err}


def parse_json(txt: str) -> list[dict]:
    """容忍模型偶尔包一层 ```json 或加解释文字。"""
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    try:
        out = json.loads(txt)
    except Exception:
        m = re.search(r"\[.*\]", txt, re.S)
        if not m:
            return []
        try:
            out = json.loads(m.group(0))
        except Exception:
            return []
    if not isinstance(out, list):
        return []
    return [b for b in out if isinstance(b, dict) and "text" in b]


def dedupe(blocks: list[dict]) -> list[dict]:
    """相邻秒、内容相同的卡片只留最早一条。"""
    blocks = sorted(blocks, key=lambda b: b.get("second", 0))
    kept: list[dict] = []
    for b in blocks:
        text = str(b.get("text", "")).strip()
        if not text:
            continue
        norm = re.sub(r"\s+", "", text)
        dup = False
        for k in kept:
            if re.sub(r"\s+", "", k["text"]) == norm and \
                    abs(b.get("second", 0) - k["second"]) <= 6:
                dup = True
                break
        if not dup:
            kept.append({"second": int(b.get("second", 0)), "text": text})
    return kept


def to_markdown(blocks: list[dict], duration: float | None) -> str:
    if not blocks:
        return ""
    lines = ["## 画面文字（视觉识别）", "",
             "> 以下内容来自画面里的文字卡，**不在口播转写中**。", ""]
    for b in blocks:
        s = b["second"]
        mm, ss = divmod(int(s), 60)
        lines.append(f"### `{mm:02d}:{ss:02d}`")
        lines.append("")
        lines.append("```text")
        lines.append(b["text"])
        lines.append("```")
        lines.append("")
    if duration:
        lines.insert(2, f"> 扫描区间 0–{int(duration)} 秒，命中 {len(blocks)} 处。")
        lines.insert(3, "")
    return "\n".join(lines)


def scan(video: str, start: float | None = None, end: float | None = None,
         batch: int = 30, workers: int = 4, api_key: str | None = None,
         out_json: str | None = None, keep_frames: bool = False,
         quiet: bool = False) -> dict:
    """扫描整段视频，返回 {frames, batches, tokens, elapsed_sec, blocks, errors}。

    可被 pipeline.py 直接调用，不经过命令行。
    """
    def say(msg: str) -> None:
        if not quiet:
            print(msg, flush=True)

    api_key = api_key or config.deepseek_key()
    if not api_key:
        raise RuntimeError("缺少 DeepSeek API key（DEEPSEEK_API_KEY 或配置文件 deepseek_api_key）")

    video = os.path.abspath(video)
    if not os.path.isfile(video):
        raise FileNotFoundError(f"视频不存在：{video}")

    out_json = out_json or os.path.join(os.path.dirname(video), "overlay_text.json")
    seq_dir = (os.path.join(os.path.dirname(video), "frames")
               if keep_frames else tempfile.mkdtemp(prefix="dy-ocr-"))
    os.makedirs(seq_dir, exist_ok=True)

    say(f"[ocr] 视频 {os.path.basename(video)}")
    t0 = time.time()
    n = extract_frames(video, seq_dir, start, end)
    say(f"[ocr] 抽帧 {n} 张（{FRAME_WIDTH}px 宽，{time.time()-t0:.1f}s）")

    files = sorted(f for f in os.listdir(seq_dir) if f.endswith(".jpg"))
    base = int(start) if start is not None else 0
    batches = [(files[i:i + batch], base + i) for i in range(0, len(files), batch)]
    say(f"[ocr] {len(batches)} 批 × 最多 {batch} 帧，{workers} 并发")

    t1 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(call_batch, fs, seq_dir, st, api_key) for fs, st in batches]
        for i, fu in enumerate(futs, 1):
            r = fu.result()
            results.append(r)
            say(f"  [ocr] {i}/{len(futs)} 批次 {r['start']}s → 命中 {len(r['blocks'])} 处"
                f"  {r.get('usage', {}).get('total_tokens', 0)} tokens")

    elapsed = time.time() - t1
    blocks = dedupe([b for r in results for b in r["blocks"]])
    total_tokens = sum(r.get("usage", {}).get("total_tokens", 0) for r in results)
    fails = [r for r in results if r.get("error")]

    payload = {
        "video": video,
        "frames": n,
        "batches": len(batches),
        "tokens": total_tokens,
        "elapsed_sec": round(elapsed, 1),
        "blocks": blocks,
        "errors": [{"start": r["start"], "error": r["error"]} for r in fails],
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    say(f"\n[ocr] ✓ 命中 {len(blocks)} 处 / 用时 {elapsed:.1f}s / {total_tokens} tokens")
    for b in blocks:
        mm, ss = divmod(int(b["second"]), 60)
        say(f"  {mm:02d}:{ss:02d}  {b['text'].replace(chr(10), ' / ')[:70]}")
    say(f"[ocr] → {out_json}")
    if fails:
        say(f"\n[ocr] ⚠ {len(fails)} 个批次失败，可加 --workers 1 重跑该区间")

    if not keep_frames:
        shutil.rmtree(seq_dir, ignore_errors=True)
    return payload


def main():
    ap = argparse.ArgumentParser(description="识别视频画面里烧死的文字（信息卡/贴纸）")
    ap.add_argument("video")
    ap.add_argument("--from", dest="start", type=float, default=None, help="起始秒")
    ap.add_argument("--to", dest="end", type=float, default=None, help="结束秒")
    ap.add_argument("--batch", type=int, default=30, help="每次请求送几帧（默认 30）")
    ap.add_argument("--workers", type=int, default=4, help="并发批次数（默认 4）")
    ap.add_argument("--out", default=None, help="输出 json 路径，默认与视频同目录")
    ap.add_argument("--md", default=None, help="同时输出 markdown 片段")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--keep-frames", action="store_true", help="保留抽出的帧，便于抽查")
    args = ap.parse_args()

    if not config.deepseek_key(args.api_key):
        sys.exit("缺少 DeepSeek API key。设 DEEPSEEK_API_KEY 环境变量，"
                 "或写进 ~/.douyin-to-obsidian.json 的 deepseek_api_key 字段。")

    payload = scan(args.video, args.start, args.end, args.batch, args.workers,
                   config.deepseek_key(args.api_key), args.out, args.keep_frames)

    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(to_markdown(payload["blocks"], args.end))
        print(f"[ocr] → {args.md}")

    if payload["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
