#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config.py — 统一配置解析，让这套脚本不绑定作者本机。

优先级（高 → 低）：
    CLI 参数  >  环境变量  >  配置文件  >  自动探测  >  内置默认

配置文件：`~/.douyin-to-obsidian.json`（可选），形如
    {
      "browser_exe": "C:/Program Files/Google/Chrome/Application/chrome.exe",
      "model_dir": "D:/models/faster-whisper-large-v3",
      "vault": "D:/Notes/Obsidian",
      "port": 9223
    }

环境变量：
    DY_BROWSER_EXE   浏览器可执行文件
    WHISPER_MODEL    faster-whisper 模型目录
    OBSIDIAN_VAULT   Obsidian vault 根目录
    DY_CDP_PORT      隔离实例的调试端口（默认 9223）
"""
from __future__ import annotations

import json
import os
import shutil
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".douyin-to-obsidian.json")

# 各平台常见浏览器位置。顺序即优先级。
_BROWSER_CANDIDATES = [
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"D:\Program Files (x86)\Chrome\App\Chrome.exe",          # Chrome++ 免安装版
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    os.path.expanduser(r"~\AppData\Local\Chromium\Application\chrome.exe"),
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def _file_cfg() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


_CFG = _file_cfg()


def get(key: str, cli_value=None, default=None):
    """按优先级取配置。"""
    if cli_value not in (None, ""):
        return cli_value
    env_key = {"browser_exe": "DY_BROWSER_EXE", "model_dir": "WHISPER_MODEL",
               "vault": "OBSIDIAN_VAULT", "port": "DY_CDP_PORT"}.get(key)
    if env_key and os.environ.get(env_key):
        v = os.environ[env_key]
        return int(v) if key == "port" else v
    if _CFG.get(key) not in (None, ""):
        return _CFG[key]
    if key == "browser_exe":
        return detect_browser()
    if key == "model_dir":
        return detect_model()
    return default


MODEL_NAMES = ("faster-whisper-large-v3", "faster-whisper-medium", "faster-whisper-small")


def detect_model() -> str | None:
    """找本机已下载的 faster-whisper 模型目录。

    顺序：本 skill 的 models/ → 同级目录下的 models/（复用别的 skill 已下的模型）
    → HuggingFace 缓存里的 `models--Systran--faster-whisper-*` 快照。
    """
    for base in (os.path.join(SKILL_DIR, "models"),
                 os.path.join(os.path.dirname(SKILL_DIR), "models")):
        for name in MODEL_NAMES:
            p = os.path.join(base, name)
            if os.path.isfile(os.path.join(p, "model.bin")):
                return p

    # 同级 skill 目录（如 video-to-skill/models/faster-whisper-large-v3）
    parent = os.path.dirname(SKILL_DIR)
    try:
        for sib in os.listdir(parent):
            for name in MODEL_NAMES:
                p = os.path.join(parent, sib, "models", name)
                if os.path.isfile(os.path.join(p, "model.bin")):
                    return p
    except OSError:
        pass

    # HF 缓存：snapshots/<hash>/ 才是真目录
    hf_home = os.environ.get("HF_HOME") or os.path.expanduser(
        "~/.cache/huggingface" if os.name != "nt" else "~/.cache/huggingface")
    hub = os.path.join(hf_home, "hub")
    try:
        for d in os.listdir(hub):
            if d.startswith("models--Systran--faster-whisper"):
                snap = os.path.join(hub, d, "snapshots")
                for h in os.listdir(snap):
                    p = os.path.join(snap, h)
                    if os.path.isfile(os.path.join(p, "model.bin")):
                        return p
    except OSError:
        pass
    return None


def detect_browser() -> str | None:
    """按候选列表 + PATH 探测可用浏览器。"""
    for p in _BROWSER_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def python_exe() -> str:
    """子进程一律用当前解释器，避免硬编码路径。"""
    return sys.executable


def deepseek_key(cli_value: str | None = None) -> str | None:
    """DeepSeek API key，用于画面文字识别（可选能力）。

    优先级：CLI > 环境变量 DEEPSEEK_API_KEY > 配置文件 deepseek_api_key。
    没有 key 不影响 ASR 链路，只是拿不到画面上的文字。
    """
    if cli_value:
        return cli_value
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    return _CFG.get("deepseek_api_key") or None


def dump() -> str:
    """`--doctor` 用：打印解析结果，方便排查。"""
    browser = get("browser_exe")
    model = get("model_dir")
    lines = [
        f"配置文件      : {CONFIG_PATH} {'(存在)' if _CFG else '(不存在，使用默认)'}",
        f"Python        : {python_exe()}",
        f"浏览器        : {browser or '✗ 未找到，请设 DY_BROWSER_EXE'}",
        f"模型目录      : {model or '✗ 未找到，请设 WHISPER_MODEL'}",
        f"  model.bin   : {'✓' if model and os.path.exists(os.path.join(model, 'model.bin')) else '✗'}",
        f"Vault         : {get('vault') or '(未配置，可设 OBSIDIAN_VAULT)'}",
        f"调试端口      : {get('port', default=9223)}",
        f"ffmpeg        : {shutil.which('ffmpeg') or '✗ 未安装'}",
        f"ffprobe       : {shutil.which('ffprobe') or '✗ 未安装'}",
    ]
    try:
        import faster_whisper  # noqa
        lines.append(f"faster-whisper: ✓ {faster_whisper.__version__}")
    except ImportError:
        lines.append("faster-whisper: ✗ 未安装（pip install faster-whisper）")
    try:
        import websocket  # noqa
        lines.append("websocket-cli : ✓")
    except ImportError:
        lines.append("websocket-cli : ✗ 未安装（pip install websocket-client）")
    k = deepseek_key()
    lines.append(f"DeepSeek key  : {'✓ 已配置（画面文字识别可用）' if k else '✗ 未配置（跳过画面文字识别）'}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(dump())
