#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr_local.py — 本地 faster-whisper 转写，音频不出机器。

为什么不用 faster-whisper 自带的文件解码：
  Python 3.14 只能装 `av 19.x`，该版本删掉了 `av.open(metadata_errors=...)`，
  而 faster-whisper 1.2.1 仍在用它 → 传文件路径必崩（TypeError）。
  降级到 `av 14.x` 在新版 Python 上没有预编译 wheel，源码编译会失败。
  解法：用系统 ffmpeg 解码成 float32 numpy 数组，直接喂 `transcribe()`。

前置：
    pip install faster-whisper numpy
    pip install nvidia-cublas-cu12 nvidia-cudnn-cu12     # GPU 必需
    模型：设 WHISPER_MODEL 指向 faster-whisper 模型目录（含 model.bin）

用法：
    python asr_local.py <文件或目录> [--out DIR] [--prompt "领域词"]
    python asr_local.py <目录> --device cpu --compute-type int8
"""
from __future__ import annotations

import argparse
import json
import os
import site
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import config  # noqa: E402

MEDIA_EXT = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm",
             ".m4a", ".mp3", ".wav", ".aac", ".opus", ".flac"}

# Whisper 在静音/纯音乐段的经典幻觉。按精确短语清，不能只匹通用词
# （口播正文本身就会讲「点赞」「订阅」）。
HALLUCINATION_MARKERS = (
    "明镜与点点", "明鏡與點點", "点点栏目", "點點欄目",
    "请不吝", "請不吝", "Amara.org", "amara.org",
    "字幕志愿者", "字幕组提供", "字幕由",
    "MING PAO", "明報", "明报",
    "Thanks for watching", "Thank you for watching",
    "Subtitles by", "subtitles by", "www.qq.com",
)


def log(m: str) -> None:
    print(f"[asr] {m}", flush=True)


def add_cuda_dlls() -> list[str]:
    """ctranslate2 在 Windows 上不会自动找到 pip 装的 cuBLAS/cuDNN，需手动加 DLL 目录。"""
    added: list[str] = []
    roots: list[str] = []
    for fn in (getattr(site, "getsitepackages", None), getattr(site, "getusersitepackages", None)):
        try:
            v = fn() if fn else None
        except Exception:
            v = None
        if isinstance(v, str):
            roots.append(v)
        elif v:
            roots += list(v)
    roots.append(os.path.dirname(os.__file__))
    for root in roots:
        for sub in (("nvidia", "cublas", "bin"), ("nvidia", "cudnn", "bin"), ("nvidia", "cuda_nvrtc", "bin")):
            p = os.path.join(root, *sub)
            if os.path.isdir(p) and p not in added:
                try:
                    os.add_dll_directory(p)
                except Exception:
                    pass
                os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]
                added.append(p)
    return added


def load_audio(path: str, sr: int = 16000):
    """ffmpeg → mono float32 numpy。替代 PyAV。"""
    import numpy as np
    cmd = ["ffmpeg", "-nostdin", "-threads", "0", "-i", path,
           "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-500:])
    return np.frombuffer(r.stdout, np.float32)


def scrub(segs: list[dict]) -> tuple[list[dict], int]:
    out, dropped = [], 0
    for s in segs:
        t = s["text"].strip()
        if not t or any(m in t for m in HALLUCINATION_MARKERS):
            dropped += 1
            continue
        if out and t == out[-1]["text"]:
            dropped += 1
            continue
        out.append(s)
    return out, dropped


def ts(sec: float) -> str:
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


def process(model, path: str, out_dir: str, prompt: str | None) -> dict:
    name = os.path.splitext(os.path.basename(path))[0]
    txt = os.path.join(out_dir, name + ".txt")
    seg = os.path.join(out_dir, name + ".segments.jsonl")
    if os.path.exists(txt) and os.path.exists(seg):
        log(f"跳过（已转写）{name}")
        return {"name": name, "status": "cached"}

    audio = load_audio(path)
    dur = len(audio) / 16000.0
    t0 = time.time()
    segments, _info = model.transcribe(
        audio, language="zh", vad_filter=True,
        condition_on_previous_text=False,      # 防长音频复读幻觉
        initial_prompt=prompt or None,
    )
    raw = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
           for s in segments]
    clean, dropped = scrub(raw)
    elapsed = time.time() - t0

    with open(seg, "w", encoding="utf-8") as f:
        for s in clean:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(txt, "w", encoding="utf-8") as f:
        for s in clean:
            f.write(f"[{ts(s['start'])}] {s['text']}\n")

    speed = dur / elapsed if elapsed else 0
    log(f"✓ {name}  {dur:.1f}s 音频 / {elapsed:.1f}s 推理 / {speed:.1f}x  "
        f"{len(clean)} 段（丢弃 {dropped}）")
    return {"name": name, "status": "done", "duration": round(dur, 1),
            "elapsed": round(elapsed, 1), "speed": round(speed, 1),
            "segments": len(clean), "txt": txt}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="视频/音频文件，或包含媒体的目录")
    ap.add_argument("--out", default=None)
    ap.add_argument("--model", default=None, help="模型目录（默认取 WHISPER_MODEL）")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--compute-type", default=None, help="默认 cuda=float16 / cpu=int8")
    ap.add_argument("--prompt", default=None, help="领域词 initial_prompt，显著降低术语错字")
    args = ap.parse_args()

    model_dir = config.get("model_dir", args.model)
    if not model_dir or not os.path.isdir(model_dir):
        log("✗ 找不到模型目录。设 WHISPER_MODEL，或用 --model 指定")
        log("  可用 fetch_model.py 从 ModelScope 拉取 large-v3")
        return 2

    ct = args.compute_type or ("float16" if args.device == "cuda" else "int8")
    if args.device == "cuda" and not add_cuda_dlls():
        log("提示：未找到 nvidia/*/bin，若 GPU 失败请加 --device cpu")

    targets: list[str] = []
    if os.path.isdir(args.target):
        for root, _d, files in os.walk(args.target):
            for fn in sorted(files):
                if os.path.splitext(fn)[1].lower() in MEDIA_EXT:
                    targets.append(os.path.join(root, fn))
    else:
        targets = [args.target]
    if not targets:
        log("✗ 没有找到可处理的媒体文件")
        return 2

    from faster_whisper import WhisperModel
    log(f"模型 {model_dir} | {args.device}/{ct} | {len(targets)} 个文件")
    t = time.time()
    model = WhisperModel(model_dir, device=args.device, compute_type=ct)
    log(f"加载耗时 {time.time() - t:.1f}s")

    results = []
    for p in targets:
        out_dir = args.out or os.path.dirname(os.path.abspath(p))
        os.makedirs(out_dir, exist_ok=True)
        try:
            results.append(process(model, p, out_dir, args.prompt))
        except Exception as e:
            log(f"✗ {os.path.basename(p)}: {type(e).__name__}: {str(e)[:200]}")
            results.append({"name": os.path.basename(p), "status": "failed"})

    ok = sum(1 for r in results if r["status"] in ("done", "cached"))
    log(f"完成 {ok}/{len(results)}")
    return 0 if ok == len(results) else 3


if __name__ == "__main__":
    sys.exit(main())
