#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili_login.py — 扫码登录 B 站，把登录态存进配置文件。

为什么需要这一步：B 站未登录时 playurl 只给到 480P，且拿不到 AI/CC 字幕。
而 Chrome 127+ 起 cookie 用了 App-Bound Encryption，**从磁盘读不出来**
（实测 Chrome 145 的 Local State 里 encrypted_key 只有 37 字节，DPAPI 解不开）。

所以改成让浏览器自己交出来：起一个**独立的**持久 profile 实例，
你在里面扫码登录一次，脚本用 CDP 直接读 cookie，不碰你日常那份浏览器。
profile 会留在本机，下次不用重扫。

用法：
    python bili_login.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from fetch_douyin import CDP, ensure_browser, shutdown, _http_json

LOGIN_URL = "https://passport.bilibili.com/login"
CHECK_URL = "https://api.bilibili.com/x/web-interface/nav"
PROFILE = os.path.join(os.path.expanduser("~"), ".vto-bili-profile")
TIMEOUT = 300


def _verify(sessdata: str) -> dict | None:
    """用 SESSDATA 调一次 nav，确认登录态真的有效。"""
    req = urllib.request.Request(
        CHECK_URL,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com",
                 "Cookie": f"SESSDATA={sessdata}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        data = d.get("data") or {}
        return data if data.get("isLogin") else None
    except Exception:
        return None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="登录 B 站并保存登录态")
    ap.add_argument("--profile", default=None,
                    help="浏览器 profile 目录；填 main 表示用日常那份（需先关闭浏览器，免扫码）")
    args = ap.parse_args()

    chrome = config.get("browser_exe")
    if not chrome or not os.path.isfile(chrome):
        print("✗ 找不到浏览器，先设 VTO_BROWSER_EXE 或跑 pipeline.py --doctor")
        return 1

    # 换个端口，避免和正在跑的抖音抓取抢
    port = int(config.get("port", default=9223)) + 1

    if args.profile and args.profile.lower() == "main":
        # 用日常 profile：浏览器里已经登录过，不需要扫码
        profile = os.path.dirname(os.path.dirname(chrome))  # ...\Chrome\App\Chrome.exe → ...\Chrome
        profile = os.path.join(profile, "Data")
        if not os.path.isdir(profile):
            print(f"✗ 猜不出日常 profile 目录（试过 {profile}），请用 --profile <路径> 指定")
            return 1
        need_scan = False
        print("=" * 58)
        print("使用你日常的浏览器 profile 读取登录态。")
        print("⚠ 请确认浏览器已完全退出，否则 profile 被占用会起不来。")
        print("=" * 58)
    else:
        profile = args.profile or PROFILE
        need_scan = True
        print("=" * 58)
        print("即将打开一个【独立的】浏览器窗口（不是你现在用的那个）")
        print("请在里面扫码登录 B 站，登录成功会自动继续，不用手动关窗口。")
        print(f"最多等 {TIMEOUT} 秒。")
        print("=" * 58)

    print(f"  profile: {profile}")
    print(f"  端口: {port}\n")

    if not ensure_browser(chrome, port, profile, LOGIN_URL if need_scan else "https://www.bilibili.com", wait=45):
        print("✗ 浏览器起不来。多半是 profile 正被占用（先关掉所有浏览器窗口再试），或端口冲突。")
        return 1

    page = None
    for _ in range(10):
        pages = [t for t in _http_json(port, "/json/list") if t.get("type") == "page"]
        page = next((p for p in pages if "bilibili" in (p.get("url") or "")), pages[0] if pages else None)
        if page:
            break
        time.sleep(1)
    if not page:
        print("✗ 找不到可连接的标签页")
        shutdown(port)
        return 1

    cdp = CDP(page["webSocketDebuggerUrl"])
    try:
        cdp.send("Network.enable")
        deadline = time.time() + TIMEOUT
        sess = None
        tries = 0
        last_hint = 0
        while time.time() < deadline:
            tries += 1
            try:
                r = cdp.send("Network.getAllCookies")
                cookies = (r.get("result") or {}).get("cookies") or []
                sess = next((c["value"] for c in cookies
                             if c["name"] == "SESSDATA" and "bilibili" in c.get("domain", "")),
                            None)
            except Exception:
                pass
            if sess:
                break
            # 用日常 profile 时不该等：本来就该有登录态，没有就是没有
            if not need_scan and tries >= 3:
                print("\n✗ 这个 profile 里没有 B 站登录态。可能没登录过，或登录已过期。")
                print("  改用扫码方式重跑：python bili_login.py")
                shutdown(port)
                return 1
            left = int(deadline - time.time())
            if left % 30 == 0 and left != last_hint:
                last_hint = left
                print(f"  …等待登录（剩余 {left}s）")
            time.sleep(2)

        if not sess:
            print("\n✗ 超时，没有检测到登录。可以重跑本脚本再试一次。")
            shutdown(port)
            return 1

        print("\n✓ 检测到 SESSDATA")
        info = _verify(sess)
        if info:
            print(f"  登录有效：{info.get('uname')}  等级 Lv{info.get('level')}"
                  f"  {'大会员' if info.get('vipStatus') else '普通用户'}")
        else:
            print("  ⚠ 用这个 SESSDATA 调接口没能通过验证，可能刚登录还没生效。")
            print("    已照样保存，稍后跑 pipeline.py --doctor 可复查。")

        path = config.save_setting("bili_sessdata", sess)
        print(f"  已写入 {path}")
        print("\n之后 B 站可下 720P/1080P，并能读 AI/CC 字幕。")
        return 0
    finally:
        cdp.close()
        shutdown(port)


if __name__ == "__main__":
    sys.exit(main())
