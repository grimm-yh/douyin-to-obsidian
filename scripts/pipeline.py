#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py — 抖音链接 → 视频 + 转写稿 + 笔记草稿，一条命令跑完。

四步，每步幂等（已有产物自动跳过）：

    S0  前置检查   浏览器 / 模型 / ffmpeg
    S1  解析+抓取  短链 → aweme_id → 独立浏览器实例抓 aweme_detail
    S2  下载       取最高码率 + 带 Referer 下载
    S3  转写       本地 faster-whisper（音频不出机器）
    S4  出草稿     按模板生成 note_draft.md（frontmatter 填好，正文待归纳）

S4 之后只剩一件事：把转写稿归纳成知识点。这一步故意不自动化——
真人抖音口播字错率约 3–4%，机器分不清「绘画结束」其实是「会话结束」。

用法：
    python pipeline.py "https://v.douyin.com/XXXX/"
    python pipeline.py "https://v.douyin.com/XXXX/" --prompt "领域术语A、术语B"
    python pipeline.py --queue queue.jsonl        # 消费 clip_watch.py 的产出
    python pipeline.py "<链接>" --skip-asr         # 只要视频
    python pipeline.py --doctor                   # 只打印环境诊断
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import config  # noqa: E402

PY = config.python_exe()
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)


# ---------------------------------------------------------------- S0

def preflight(need_browser: bool = True) -> bool:
    """need_browser=False 时（纯 B 站任务）不强制要求浏览器。"""
    ok = True
    if need_browser:
        exe = config.get("browser_exe")
        if not exe or not os.path.isfile(exe):
            log("S0", "✗ 找不到浏览器，设 DY_BROWSER_EXE 或写入 ~/.video-to-obsidian.json")
            ok = False
    model = config.get("model_dir")
    if not model or not os.path.isdir(model):
        log("S0", "✗ 找不到模型目录，设 WHISPER_MODEL（可用 fetch_model.py 下载）"); ok = False
    import shutil
    for b in ("ffmpeg", "ffprobe"):
        if not shutil.which(b):
            log("S0", f"✗ 缺少 {b}"); ok = False
    if ok:
        log("S0", "✓ " + ("浏览器 / 模型 / ffmpeg 齐备" if need_browser else "模型 / ffmpeg 齐备"))
    return ok


# ---------------------------------------------------------------- S1

def resolve_aweme_id(url: str) -> str | None:
    """短链 → aweme_id。优先跟 302，其次从 HTML 里捞。不需要浏览器。"""
    m = re.search(r"/video/(\d+)", url) or re.search(r"/share/video/(\d+)", url)
    if m:
        return m.group(1)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            final = r.geturl()
            m = re.search(r"/video/(\d+)", final) or re.search(r"/share/video/(\d+)", final)
            if m:
                return m.group(1)
            body = r.read(400_000).decode("utf-8", "replace")
            for pat in (r'"aweme_id"\s*:\s*"?(\d{15,})', r"/video/(\d{15,})"):
                m = re.search(pat, body)
                if m:
                    return m.group(1)
    except Exception as e:
        log("S1", f"短链解析异常：{type(e).__name__}: {e}")
    return None


def step_collect(aid: str, workdir: str, fresh: bool) -> str | None:
    out = os.path.join(workdir, f"dy-{aid}.api.json")
    if os.path.exists(out) and not fresh:
        log("S1", f"跳过（已存在 {os.path.basename(out)}）")
        return out
    log("S1", "启动隔离浏览器实例抓取（不影响你正在用的浏览器）…")
    r = subprocess.run([PY, os.path.join(HERE, "fetch_douyin.py"), aid, "--outdir", workdir],
                       capture_output=True)
    txt = (r.stdout + r.stderr).decode("utf-8", "replace")
    if r.returncode != 0 or not os.path.exists(out):
        log("S1", "✗ 没抓到 aweme 详情")
        for line in txt.strip().splitlines()[-6:]:
            log("S1", "   " + line.strip())
        return None
    log("S1", f"✓ {os.path.basename(out)}  {os.path.getsize(out) / 1024:.0f}KB")
    return out


# ---------------------------------------------------------------- S2

def step_download(api_json: str, workdir: str) -> str | None:
    dl_root = os.path.join(workdir, "downloads")
    r = subprocess.run([PY, os.path.join(HERE, "douyin_dl.py"), api_json, "--out", dl_root],
                       capture_output=True)
    txt = (r.stdout + r.stderr).decode("utf-8", "replace")
    for line in txt.strip().splitlines():
        log("S2", line.strip())
    if r.returncode != 0:
        return None
    m = re.search(r"douyin-(\d+)", txt)
    return os.path.join(dl_root, f"douyin-{m.group(1)}") if m else None


# ---------------------------------------------------------------- S3

def step_asr(folder: str, prompt: str | None) -> str | None:
    video = os.path.join(folder, "video.mp4")
    if not os.path.exists(video):
        log("S3", f"✗ 找不到 {video}")
        return None
    cmd = [PY, os.path.join(HERE, "asr_local.py"), video]
    if prompt:
        cmd += ["--prompt", prompt]
    r = subprocess.run(cmd, capture_output=True)
    txt = (r.stdout + r.stderr).decode("utf-8", "replace")
    for line in txt.strip().splitlines()[-4:]:
        log("S3", line.strip())
    out = os.path.join(folder, "video.txt")
    return out if os.path.exists(out) else None


# ---------------------------------------------------------------- S4

def make_title(desc: str, fallback: str) -> str:
    """抖音 desc 是整段文案，标题通常在双空格/换行之前。
    顺序：去 hashtag → 按双空格切 → 仍超长按句号切 → 硬截 40。"""
    t = re.sub(r"#\S+", "", desc).strip()
    parts = [p.strip() for p in re.split(r"\s{2,}|\n", t) if p.strip()]
    cand = parts[0] if parts else t
    if len(cand) > 40:
        cand = re.split(r"[。！？]", cand)[0]
    if len(cand) > 40:
        cand = cand[:40].rstrip("，,、 ")
    return cand or fallback


def pick_tags(desc: str) -> list[str]:
    seen, out = set(), []
    for t in re.findall(r"#([^\s#]{1,20})", desc):
        if t not in seen:
            seen.add(t); out.append(t)
    return out[:5] or ["待归类"]


def step_draft(folder: str, source_url: str, overlay: list[dict] | None = None) -> str | None:
    meta_p = os.path.join(folder, "metadata.json")
    txt_p = os.path.join(folder, "video.txt")
    meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
    desc = (meta.get("desc") or "").strip()
    author = (meta.get("author") or "").strip()
    dur_s = round((meta.get("duration_ms") or 0) / 1000)
    title = make_title(desc, f"抖音视频-{meta.get('aweme_id')}")
    tags = pick_tags(desc)
    body = open(txt_p, encoding="utf-8").read() if os.path.exists(txt_p) else "（未转写）"

    # 画面文字单独成节，插在原文转写之前
    overlay_sec: list[str] = []
    asr_no = "五"
    if overlay:
        asr_no = "六"
        overlay_sec = [
            "## 五、画面文字（视觉识别）",
            "",
            "> 以下内容来自画面里的文字卡，**不在口播转写中**。归纳配方/参数时优先用它。",
            "",
        ]
        for b in overlay:
            mm, ss = divmod(int(b["second"]), 60)
            overlay_sec += [f"### `{mm:02d}:{ss:02d}`", "", "```text", b["text"], "```", ""]

    lines = [
        "---",
        f"标题: {title}",
        f"来源: {source_url}",
        f"作者: {author}",
        f"创建时间: {time.strftime('%Y-%m-%d')}",
        "状态: 待复核",
        "标签:",
        *[f"  - {t}" for t in tags],
        "---",
        "",
        f"# {title}",
        "",
        "> **一句话结论**",
        "> （待填）",
        "",
        "## 一、待填",
        "",
        "## 二、待填",
        "",
        "## 三、可执行动作",
        "",
        "- [ ] 待填",
        "",
        "## 四、存疑 / 未验证",
        "",
        "- （待填）",
        "",
        *overlay_sec,
        f"## {asr_no}、原文转写",
        "",
        "> [!note]- 带时间戳全文（点击展开）",
        *["> " + ln for ln in body.strip().splitlines()],
        "",
        "---",
        "",
        "> [!warning] 质量闸门",
        "> 转写自本地 faster-whisper。真人抖音口播字错率约 3–4%，",
        "> 关键结论必须回看原片核对，**未经复核前不要当作定论引用**。",
    ]
    if overlay:
        lines.append("> 画面文字由视觉模型识别，已抽帧目视复核。")
    lines.append("")

    out = os.path.join(folder, "note_draft.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("S4", f"✓ 草稿 {out}")
    log("S4", f"  标题: {title}")
    log("S4", f"  标签: {', '.join(tags)}（取自 hashtag，需人工确认）")
    log("S4", f"  时长: {dur_s}s  正文待归纳")
    if overlay:
        log("S4", f"  画面文字: {len(overlay)} 处已并入草稿")
    return out


# ---------------------------------------------------------------- 平台分派

def detect_platform(url: str) -> str | None:
    """只认抖音和 B 站，其余一律返回 None 让上层拒绝。"""
    u = url.lower()
    if "douyin.com" in u or "iesdouyin.com" in u:
        return "douyin"
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    return None


def stage_douyin(url: str, workdir: str, fresh: bool) -> tuple[str | None, str]:
    """抖音的 S1+S2：CDP 抓接口 + ffmpeg 下载。返回 (folder, stage)。"""
    log("S1", f"处理 {url}")
    aid = resolve_aweme_id(url)
    if not aid:
        log("S1", "✗ 无法解析 aweme_id（链接失效 / 非抖音 / 网络问题）")
        return None, "S1"
    log("S1", f"✓ aweme_id = {aid}")

    api = step_collect(aid, workdir, fresh)
    if not api:
        return None, "S1"
    folder = step_download(api, workdir)
    if not folder:
        log("S2", "✗ 下载失败")
        return None, "S2"
    return folder, "S2"


def stage_bilibili(url: str, workdir: str, page: int | None,
                   fresh: bool = False) -> tuple[str | None, str]:
    """B 站的 S1+S2：公开 API，不需要浏览器。返回 (folder, stage)。"""
    log("S1", f"处理 {url}")
    try:
        import bili_fetch
        # bili_fetch 的 log 只收一条消息，这里适配成 pipeline 的两段式
        r = bili_fetch.fetch(url, workdir, config.bili_sessdata(), page,
                             log=lambda m: log("S1", m), fresh=fresh)
    except Exception as e:
        # BiliError 自带人话说明；其他异常也一并兜住，不让整个队列中断
        log("S1", f"✗ {e}")
        return None, "S1"
    return r["folder"], "S2"


# ---------------------------------------------------------------- 主流程

def run_one(url: str, workdir: str, skip_asr: bool, fresh: bool,
            prompt: str | None, ocr: bool = False, page: int | None = None) -> dict:
    res = {"url": url, "status": "failed", "stage": "S1"}

    plat = detect_platform(url)
    if not plat:
        log("S1", "✗ 无法识别的链接。只支持抖音和 B 站，例如：")
        log("S1", "    https://v.douyin.com/xxxxxxx/")
        log("S1", "    https://www.bilibili.com/video/BV1xxxxxxxxx")
        return res
    res["platform"] = plat

    if plat == "douyin":
        folder, stage = stage_douyin(url, workdir, fresh)
    else:
        folder, stage = stage_bilibili(url, workdir, page, fresh)
    res["stage"] = stage
    if not folder:
        return res
    res["folder"] = folder
    res["stage"] = "S3"

    if not skip_asr:
        txt = step_asr(folder, prompt)
        if not txt:
            log("S3", "✗ 转写失败")
            return res
    else:
        log("S3", "已跳过转写（--skip-asr）")

    # 画面文字：口播拿不到的信息（配方克数、参数表）只能从这里来
    overlay = None
    if ocr:
        res["stage"] = "S3b"
        video = next((os.path.join(folder, f) for f in os.listdir(folder)
                      if f.lower().endswith(".mp4")), None)
        if video:
            try:
                import ocr_frames
                overlay = ocr_frames.scan(video, quiet=False)["blocks"]
                res["overlay"] = len(overlay)
            except Exception as e:
                log("S3b", f"✗ 画面文字识别失败（不影响其他环节）：{e}")
        else:
            log("S3b", "✗ 没找到视频文件")

    if skip_asr and not ocr:
        res["status"] = "partial"
        return res

    res["stage"] = "S4"
    draft = step_draft(folder, url, overlay)
    res["draft"] = draft
    res["status"] = "done" if draft else "partial"
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", help="抖音链接（短链或网页版都可）")
    ap.add_argument("--queue", help="消费 clip_watch.py 产出的 queue.jsonl")
    ap.add_argument("--workdir", default=os.getcwd())
    ap.add_argument("--skip-asr", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="忽略已有产物强制重抓")
    ap.add_argument("--prompt", default=None, help="ASR 领域词，显著降低术语错字")
    ap.add_argument("--ocr", action="store_true",
                    help="识别画面上的文字卡（配方克数、参数表）。需 DeepSeek key，按 token 计费")
    ap.add_argument("--page", type=int, default=None,
                    help="B 站多 P 视频取第几 P（默认第 1P）")
    ap.add_argument("--doctor", action="store_true", help="只打印环境诊断")
    args = ap.parse_args()

    if args.doctor:
        print(config.dump())
        return 0
    if not args.url and not args.queue:
        ap.print_help()
        return 2

    urls: list[str] = []
    if args.queue:
        if not os.path.exists(args.queue):
            log("S0", f"✗ 队列文件不存在 {args.queue}")
            return 2
        seen = set()
        for line in open(args.queue, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                u = json.loads(line)["url"]
            except Exception:
                continue
            if u not in seen:
                seen.add(u); urls.append(u)
    if args.url:
        urls.insert(0, args.url)

    # 只有要处理抖音链接时才强制要求浏览器；纯 B 站任务不需要
    need_browser = any(detect_platform(u) == "douyin" for u in urls)
    if not preflight(need_browser):
        log("S0", "前置检查未通过，已停止")
        return 3

    log("S0", f"共 {len(urls)} 条待处理")
    results = [run_one(u, args.workdir, args.skip_asr, args.fresh, args.prompt,
                       args.ocr, args.page)
               for u in urls]

    print("\n" + "=" * 60)
    ok = sum(1 for r in results if r["status"] == "done")
    for r in results:
        flag = {"done": "✓", "partial": "~", "failed": "✗"}[r["status"]]
        plat = r.get("platform", "?")
        print(f"{flag} [{plat}] {r['url']}  [{r['status']}, 停在 {r['stage']}]")
        if r.get("draft"):
            print(f"    草稿 → {r['draft']}")
    if not args.ocr and ok > 0 and config.deepseek_key():
        print("\n提示：加 --ocr 可识别画面上的文字卡（作者常把配方/参数打在屏幕上而不念出来）。")
    print(f"\n完成 {ok}/{len(results)}。草稿需补正文后移入 Obsidian 收件箱。")
    return 0 if ok == len(results) else 3


if __name__ == "__main__":
    sys.exit(main())
