#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_model.py — 从 ModelScope 拉 faster-whisper 模型到本地。

为什么不用 huggingface_hub：国内 hf-mirror 实测 <0.3 MB/s 且会中途停住；
ModelScope 同文件实测 17–36 MB/s。

用法：
    python fetch_model.py                      # 默认 large-v3 → ./models/faster-whisper-large-v3
    python fetch_model.py --probe-only         # 只看远端文件大小，不下载
    python fetch_model.py --repo pengzhendong/faster-whisper-large-v3 --dest D:/models/lv3

说明：目前 ModelScope 上只有 large-v3 镜像实测可用（small / medium 的公开 repo 均 404）。
large-v3 是 3.08GB，CPU 上跑不动，需要 GPU + `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

FILES = ["config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.json", "model.bin"]
API = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={f}"


def log(m: str) -> None:
    print(f"[model] {m}", flush=True)


def _open(url: str, extra: dict | None = None):
    h = {"User-Agent": "Mozilla/5.0"}
    if extra:
        h.update(extra)
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60)


def download(repo: str, name: str, dest: str) -> int:
    out = os.path.join(dest, name)
    tmp = out + ".part"
    with _open(API.format(repo=repo, f=name)) as r:
        total = int(r.headers.get("Content-Length") or 0)
        got, t0, last = 0, time.time(), 0.0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                now = time.time()
                if now - last > 3 or got == total:
                    last = now
                    sp = got / max(now - t0, 1e-6) / 1048576
                    pct = got / total * 100 if total else 0
                    log(f"  {name:<24} {got / 1048576:7.1f}/{total / 1048576:.1f} MB "
                        f"({pct:5.1f}%) {sp:5.1f} MB/s")
    if total and got != total:
        os.replace(tmp, out) if False else None
        raise RuntimeError(f"{name} 大小不符：got {got} want {total}")
    os.replace(tmp, out)
    log(f"  [OK] {name}  {os.path.getsize(out)} B")
    return os.path.getsize(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="pengzhendong/faster-whisper-large-v3")
    ap.add_argument("--dest", default=None, help="默认 <skill>/models/faster-whisper-large-v3")
    ap.add_argument("--probe-only", action="store_true")
    args = ap.parse_args()

    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dest = args.dest or os.path.join(skill_dir, "models", "faster-whisper-large-v3")

    if args.probe_only:
        log(f"探测 {args.repo}")
        for name in FILES:
            try:
                with _open(API.format(repo=args.repo, f=name), {"Range": "bytes=0-1023"}) as r:
                    log(f"  {name:<24} http={r.status} {r.headers.get('Content-Range')}")
            except Exception as e:
                log(f"  {name:<24} ✗ {type(e).__name__}: {e}")
        return 0

    os.makedirs(dest, exist_ok=True)
    log(f"源 {args.repo} → {os.path.abspath(dest)}")
    t0 = time.time()
    for name in FILES:
        if os.path.exists(os.path.join(dest, name)):
            log(f"  [skip] {name} 已存在")
            continue
        download(args.repo, name, dest)

    log(f"完成，用时 {time.time() - t0:.0f}s")
    log(f"设置环境变量：WHISPER_MODEL={os.path.abspath(dest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
