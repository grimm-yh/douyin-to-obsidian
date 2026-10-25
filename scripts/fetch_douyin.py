#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_douyin.py — 抓抖音视频详情，不干扰用户正在使用的浏览器。

原理：抖音网页版对 `aweme/v1/web/aweme/detail/` 的响应里有 `aweme_detail`，
含标题、作者、时长和 20 档码率的播放地址。但这个接口要 `a_bogus` 签名，
HTTP 直取拿不到（实测返回的 HTML 里 `play_addr` 出现 0 次、无 `RENDER_DATA`）。

因此：启动一个**独立的**浏览器实例（全新 user-data-dir + 独立端口），
让页面自己发请求、自己算签名，我们只读它返回的响应。不触碰风控。

为什么不复用用户已经开着的浏览器：
  1) 日常实例没开调试端口，附加不上去
  2) 要求用户每次退出浏览器不可接受
  3) 独立 user-data-dir 与现有会话完全隔离，互不影响

⚠️ 启动 / 抓取 / 关闭必须在同一进程内串行完成：
   Popen 拉起的子进程树会随父进程退出被清理，导致端口瞬间消失。

用法：
    python fetch_douyin.py <aweme_id> [--outdir DIR] [--port 9223]
    python fetch_douyin.py <aweme_id> --keep-browser     # 抓完不关，调试用

产出：`<outdir>/dy-<aweme_id>.api.json`，内容为
    [{"url": "...", "data": {"aweme_detail": {...}}}]
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import config  # noqa: E402


def log(m: str) -> None:
    print(f"[fetch] {m}", flush=True)


# ---------------------------------------------------------------- CDP 客户端

class CDP:
    """最小可用的 Chrome DevTools Protocol 客户端。"""

    def __init__(self, ws_url: str, recv_timeout: float = 120.0):
        import websocket  # websocket-client
        self.ws = websocket.create_connection(ws_url, timeout=recv_timeout, suppress_origin=True)
        self.ws.settimeout(recv_timeout)
        self._id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self.events: list[dict] = []
        self._alive = True
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self) -> None:
        while self._alive:
            try:
                raw = self.ws.recv()
            except Exception:
                self._alive = False
                return
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            mid = msg.get("id")
            if mid is not None:
                q = self._pending.pop(mid, None)
                if q:
                    q.put(msg)
            elif msg.get("method"):
                self.events.append(msg)

    def send(self, method: str, params: dict | None = None, timeout: float = 60.0):
        with self._lock:
            self._id += 1
            mid = self._id
        q: queue.Queue = queue.Queue()
        self._pending[mid] = q
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(mid, None)
            raise TimeoutError(f"CDP 调用超时: {method}")
        if "error" in msg:
            raise RuntimeError(f"CDP {method} 失败: {json.dumps(msg['error'], ensure_ascii=False)}")
        return msg.get("result", {})

    def close(self) -> None:
        self._alive = False
        try:
            self.ws.close()
        except Exception:
            pass


def _http_json(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- 浏览器生命周期

def _version(port: int) -> dict | None:
    try:
        return _http_json(port, "/json/version")
    except Exception:
        return None


def ensure_browser(exe: str, port: int, profile: str, first_url: str, wait: int = 30) -> bool:
    if _version(port):
        log(f"复用已存在的实例 :{port}")
        return True
    os.makedirs(profile, exist_ok=True)   # Chromium 136+ 不给 --user-data-dir 会禁用调试端口
    args = [exe,
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-popup-blocking", "--mute-audio",
            first_url]
    log("启动独立实例（不影响你正在用的浏览器）")
    subprocess.Popen(args, creationflags=0x00000008 | 0x00000200, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i in range(wait):
        time.sleep(1)
        if _version(port):
            log(f"端口就绪，用时 {i + 1}s")
            return True
    log("✗ 端口未就绪")
    return False


def shutdown(port: int) -> None:
    """用 CDP 的 Browser.close 优雅关闭，比 taskkill 精确。"""
    try:
        v = _version(port)
        if not v:
            return
        import websocket
        w = websocket.create_connection(v["webSocketDebuggerUrl"], timeout=5)
        w.send(json.dumps({"id": 1, "method": "Browser.close"}))
        w.close()
        log("已关闭实例")
    except Exception as e:
        log(f"关闭实例失败（可忽略）：{type(e).__name__}")


# ---------------------------------------------------------------- 抓取

def pick_page(port: int, fragment: str) -> dict | None:
    pages = [t for t in _http_json(port, "/json/list") if t.get("type") == "page"]
    frag = (fragment or "").lower()
    for p in pages:
        if frag in (p.get("url") or "").lower():
            return p
    # 退而求其次：随便找个 douyin 页，或第一个页面
    for p in pages:
        if "douyin" in (p.get("url") or "").lower():
            return p
    return pages[0] if pages else None


def collect_detail(port: int, aweme_id: str, page_url: str, wait: int, timeout: int = 60) -> list[dict]:
    page = pick_page(port, f"douyin.com/video/{aweme_id}")
    if not page:
        log("✗ 找不到可用标签页")
        return []
    log(f"附着标签页：{(page.get('url') or '')[:90]}")
    cdp = CDP(page["webSocketDebuggerUrl"])
    out: list[dict] = []
    try:
        cdp.send("Network.enable")
        cdp.send("Page.enable")
        log(f"导航到：{page_url}")
        cdp.send("Page.navigate", {"url": page_url})

        seen: set[str] = set()
        start = time.time()
        last_hit = 0.0
        # 至少要等页面渲染（wait 秒），拿到结果后再多等 6 秒收集重复响应
        while True:
            time.sleep(0.4)
            for ev in list(cdp.events):
                if ev.get("method") != "Network.responseReceived":
                    continue
                p = ev["params"]
                url = (p.get("response") or {}).get("url") or ""
                if "aweme/detail" not in url:
                    continue
                rid = p.get("requestId")
                if rid in seen:
                    continue
                seen.add(rid)
                try:
                    body = cdp.send("Network.getResponseBody", {"requestId": rid})
                except Exception as e:
                    log(f"  取响应体失败：{type(e).__name__}")
                    continue
                raw = body.get("body") or ""
                if body.get("base64Encoded"):
                    import base64
                    raw = base64.b64decode(raw).decode("utf-8", "replace")
                try:
                    data = json.loads(raw)
                except ValueError:
                    log("  响应不是合法 JSON，跳过")
                    continue
                if (data or {}).get("aweme_detail"):
                    out.append({"url": url, "data": data})
                    last_hit = time.time()
                    log("  ✓ 拿到 aweme_detail")

            elapsed = time.time() - start
            if out and (time.time() - last_hit > 5):
                break
            if elapsed > timeout:
                log(f"  超时 {timeout}s，收集到 {len(out)} 条")
                break
            if elapsed > wait and not out and elapsed > timeout * 0.6:
                break
    finally:
        cdp.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("aweme_id")
    ap.add_argument("--outdir", default=os.getcwd())
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--exe", default=None, help="浏览器路径（默认自动探测）")
    ap.add_argument("--profile", default=None, help="隔离实例的 user-data-dir")
    ap.add_argument("--wait", type=int, default=20, help="页面渲染等待秒数")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--keep-browser", action="store_true")
    args = ap.parse_args()

    exe = config.get("browser_exe", args.exe)
    port = config.get("port", args.port, default=9223)
    profile = args.profile or os.path.join(
        os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp", "dy-cdp-profile")

    if not exe or not os.path.isfile(exe):
        log("✗ 找不到浏览器。设置 DY_BROWSER_EXE 环境变量，或写入 ~/.douyin-to-obsidian.json")
        log("  运行 `python config.py` 可查看当前解析结果")
        return 2

    aid = args.aweme_id
    page = f"https://www.douyin.com/video/{aid}"
    api_out = os.path.join(args.outdir, f"dy-{aid}.api.json")
    os.makedirs(args.outdir, exist_ok=True)

    try:
        if not ensure_browser(exe, port, profile, page):
            return 3
        time.sleep(2)
        items = collect_detail(port, aid, page, args.wait, args.timeout)
        if not items:
            log("✗ 未捕获到 aweme_detail（视频已删 / 页面未渲染 / 网络异常）")
            return 4
        with open(api_out, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        log(f"✓ {os.path.basename(api_out)}  {os.path.getsize(api_out) / 1024:.0f}KB  ({len(items)} 条)")
        return 0
    finally:
        if not args.keep_browser:
            shutdown(port)


if __name__ == "__main__":
    sys.exit(main())
