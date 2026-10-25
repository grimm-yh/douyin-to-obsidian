#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clip_watch.py — 监听剪贴板里的抖音链接，写进队列（仅 Windows）。

为什么走剪贴板：抖音分享面板的第一个选项就是「复制链接」。
手机→电脑剪贴板一旦同步，投递动作就退化成一次复制，
不需要打开任何 app、不需要选收件人、不依赖任何第三方平台。

⚠️ 读取剪贴板必须显式声明 argtypes/restype：
   `GetClipboardData` 返回 64 位 HANDLE，ctypes 默认 restype 是 32 位 c_int，
   句柄会被截断 → `GlobalLock` 拿到空指针 → `wstring_at` 报
   `OSError: access violation reading 0x0`。

用法：
    python clip_watch.py --once          # 只读一次，打印命中
    python clip_watch.py                 # 常驻监听（默认 2s 轮询）
    python clip_watch.py --interval 3 --queue queue.jsonl
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
import time
from ctypes import wintypes as w

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

CF_UNICODETEXT = 13
PATTERNS = (
    re.compile(r"https?://v\.douyin\.com/[\w\-]+/?"),
    re.compile(r"https?://www\.douyin\.com/video/\d+"),
    re.compile(r"https?://www\.iesdouyin\.com/share/video/\d+"),
)


def _setup() -> None:
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.OpenClipboard.argtypes = [w.HWND]; u.OpenClipboard.restype = w.BOOL
    u.GetClipboardData.argtypes = [w.UINT]; u.GetClipboardData.restype = w.HANDLE
    u.IsClipboardFormatAvailable.argtypes = [w.UINT]; u.IsClipboardFormatAvailable.restype = w.BOOL
    u.CloseClipboard.restype = w.BOOL
    k.GlobalLock.argtypes = [w.HANDLE]; k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalUnlock.argtypes = [w.HANDLE]; k.GlobalUnlock.restype = w.BOOL


def read_clipboard() -> str:
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    if not u.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return ""
    for _ in range(5):                      # 剪贴板可能被别的进程短暂占用
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return ""
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k.GlobalLock(h)
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def extract(text: str) -> list[str]:
    out = []
    for pat in PATTERNS:
        for m in pat.finditer(text):
            if m.group(0) not in out:
                out.append(m.group(0))
    return out


def load_seen(queue: str) -> set[str]:
    seen: set[str] = set()
    if not os.path.exists(queue):
        return seen
    with open(queue, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line)["url"])
            except Exception:
                pass
    return seen


def main() -> int:
    if os.name != "nt":
        print("[clip] 仅支持 Windows", flush=True)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--queue", default=os.path.join(os.getcwd(), "queue.jsonl"))
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    _setup()
    seen = load_seen(args.queue)

    if args.once:
        urls = extract(read_clipboard())
        print(f"剪贴板命中 {len(urls)} 条")
        for u in urls:
            print(("  [已入队] " if u in seen else "  [新] ") + u)
        return 0

    print(f"[clip] 监听中（{args.interval}s）→ {args.queue}", flush=True)
    print("[clip] 复制一条抖音链接试试。Ctrl+C 结束。", flush=True)
    last = ""
    try:
        while True:
            text = read_clipboard()
            if text and text != last:
                last = text
                for u in extract(text):
                    if u in seen:
                        continue
                    seen.add(u)
                    with open(args.queue, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                            "url": u, "raw": text[:300]}, ensure_ascii=False) + "\n")
                    print(f"[+] 入队 {u}", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n[clip] 停止。累计 {len(seen)} 条", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
