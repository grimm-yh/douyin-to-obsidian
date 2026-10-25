# douyin-to-obsidian

把抖音链接变成结构化 Obsidian 笔记的一条命令流水线。

**下载 → 本地转写（音频不出机器）→ 生成带中文 frontmatter 的笔记草稿。**
不需要登录、不需要调用任何云 API、不需要退出你正在用的浏览器。

```
抖音链接
  → [S1] 短链解析 + 隔离浏览器实例抓 aweme_detail
  → [S2] ffmpeg 带 Referer 下最高码率
  → [S3] 本地 faster-whisper 转写
  → [S4] 按模板生成 note_draft.md
  → [人工/AI] 归纳正文 → 移入 Obsidian 收件箱
```

它同时是一个 **Claude / WorkBuddy / Codex 通用 Skill**：`SKILL.md` 描述了什么情况下该用、怎么用、有哪些坑，任何支持 skill 的 agent 都能直接调用。

---

## 为什么不能更简单（省得你重走弯路）

这三条路我都试过，全部失败，结论写在 `SKILL.md` 里：

| 想法 | 为什么不行 |
|---|---|
| `yt-dlp` 直接下 | 抖音 `/aweme/v1/web/aweme/detail/` 返回 **403**。根因是没实现 `a_bogus` 签名。**带真登录 cookie 也照样 403** |
| HTTP 直取页面扒 `_ROUTER_DATA` | 返回 72KB HTML 里 `play_addr` 出现 **0 次**，`RENDER_DATA` 不存在 |
| 无头浏览器 / 伪造签名 | 属于绕过平台访问控制，违反 ToS，且平台一改就失效 |

**唯一可行**：起一个**隔离的**真实浏览器实例（独立 `--user-data-dir`、非默认端口），让页面自己算签名、自己发请求，脚本只读它返回的响应。

等价于人工打开 DevTools 看 Network 面板 —— 不伪造、不注入、不触碰风控。

这样做还有两个副作用，都是好处：

- **不需要登录**：实测未登录态也能拿到完整 `aweme_detail`
- **不占用你的浏览器**：隔离实例，你自己那份 Chrome 不用退、不用关、profile 不受影响

---

## 安装

```bash
git clone https://github.com/grimm-yh/douyin-to-obsidian.git
cd douyin-to-obsidian

pip install faster-whisper numpy websocket-client
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12   # GPU 必需；纯 CPU 可跳过
python scripts/fetch_model.py                     # 拉 3.08GB large-v3 到 ./models/
```

系统依赖：

- `ffmpeg`、`ffprobe` 在 `PATH` 中
- 一个 Chromium 系浏览器（Chrome / Chromium / Edge / Chrome++ 均可，自动探测）

环境诊断：

```bash
python scripts/pipeline.py --doctor
```

```
配置文件      : ~/.douyin-to-obsidian.json (不存在，使用默认)
Python        : C:\Python314\python.exe
浏览器        : D:\Program Files (x86)\Chrome\App\Chrome.exe
模型目录      : ...\video-to-skill\models\faster-whisper-large-v3
  model.bin   : ✓
ffmpeg        : ✓
faster-whisper: ✓ 1.2.1
websocket-cli : ✓
```

---

## 用法

```bash
# 完整链路
python scripts/pipeline.py "https://v.douyin.com/XXXX/"

# 带领域词 —— 强烈建议，专业术语错字明显减少
python scripts/pipeline.py "<链接>" --prompt "AI编程,agent,上下文压缩"

# 只要视频，不转写
python scripts/pipeline.py "<链接>" --skip-asr

# 批量消费剪贴板队列
python scripts/pipeline.py --queue queue.jsonl
```

剪贴板入口（仅 Windows，手机上「复制链接」即完成投递）：

```bash
python scripts/clip_watch.py     # 常驻监听，识别到抖音链接就写 queue.jsonl
```

---

## 输出契约

```text
<workdir>/
├── dy-<aweme_id>.api.json               # 原始接口响应（排错用）
└── downloads/douyin-<aweme_id>/
    ├── video.mp4                        # 最高可用码率
    ├── metadata.json                    # 标题/作者/时长/全部码率档
    ├── post_caption.txt                 # 原始文案（含 hashtag）
    ├── video.txt                        # 带 [mm:ss] 时间戳的转写
    ├── video.segments.jsonl             # 分段 {start,end,text}
    └── note_draft.md                    # 笔记草稿，frontmatter 已填好
```

`note_draft.md` 的 frontmatter 是固定的 6 个中文字段：

```yaml
---
标题: 油泼辣子-香而不燥红润油亮
来源: https://v.douyin.com/aDQ_ck64ans/
作者: 二婶儿教做菜（带着二叔）
创建时间: 2026-10-25
状态: 待复核
标签:
  - 家常菜
  - 调味料
---
```

> **S4 之后必须补正文，不要直接入库。** 草稿只有骨架，正文需要 AI 归纳或人工整理。为什么见下面的「已知限制」。

---

## 已知限制（如实说明）

这是这套工具最重要的部分。**如果你需要的是「精确数字」，它给不了。**

| 限制 | 实测数据 | 应对 |
|---|---|---|
| **真人口播字错率** | **3–4%**（TTS 清晰朗读约 1.4%） | 草稿强制标 `状态: 待复核`；关键数字与专有名词必须回看原片 |
| 典型错例 | 「碗的1分钟」→ 应为 1/2；「酒的扩重」→ 克重；「装好封颜色」→ 封严 | 归纳时在「存疑」一节显式列出 |
| **硬字幕拿不到** | 烧在画面里的文字（配方克数、PPT、代码）纯 ASR **采不到** | 需视觉模型 OCR，属另一条链路，本项目不含 |
| 网页免费档码率 | 单视频页最高 `normal_1080_0`（1080p / ~680kbps）；**合集页只到 720p** | 别指望高码率原片 |
| 短句破碎 | 抖音口播节奏快，155 段 / 245 秒是常态（约 2.3s 一段） | 归纳时合并同义段，别逐段抄 |
| 地址时效 | 播放地址带 `cdn_url_expired` | 抓完尽快下载，别搁置 |

一个真实例子：一条油泼辣子教程里，辣椒面和白芝麻的克数写在画面上但没念出来，转写里**这两个数字直接缺失**。这是能力边界，不是 bug。

性能参考：large-v3 在 RTX 显卡上约 **5–5.5 倍实时**（34 秒音频 → 6.2 秒推理）。纯 CPU 慢 10–20 倍。

---

## 配置

优先级：**CLI 参数 > 环境变量 > 配置文件 > 自动探测**。

```bash
export DY_BROWSER_EXE="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
export WHISPER_MODEL="/path/to/faster-whisper-large-v3"
export OBSIDIAN_VAULT="/path/to/vault"    # 可选
export DY_CDP_PORT=9223                    # 可选，隔离实例端口
```

或写 `~/.douyin-to-obsidian.json`：

```json
{
  "browser_exe": "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "model_dir": "D:/models/faster-whisper-large-v3",
  "vault": "D:/Notes/Obsidian",
  "port": 9223
}
```

自动探测会找常见浏览器路径，也会**复用本机其他 skill 已下载的模型**（扫描同级目录与 HuggingFace 缓存），所以很多时候不用配。

也支持 macOS / Linux：浏览器探测覆盖 `/Applications/*`、`/usr/bin/google-chrome` 等；剪贴板监听目前仅 Windows。

---

## 目录结构

```text
douyin-to-obsidian/
├── SKILL.md                    # agent 读的说明书：何时用、怎么用、边界在哪
├── references/
│   ├── note-template.md        # frontmatter 字段 + 排版规范
│   └── troubleshooting.md      # 症状 → 根因 → 处置
├── scripts/
│   ├── pipeline.py             # 主入口，串 S0–S4
│   ├── config.py               # 统一配置解析（跨平台自动探测）
│   ├── fetch_douyin.py         # 起隔离浏览器 + 内联 CDP 抓接口响应
│   ├── douyin_dl.py            # 解析 bit_rate[] 取最高档 + ffmpeg 下载
│   ├── asr_local.py            # faster-whisper 转写（绕开 PyAV）
│   ├── fetch_model.py          # 下载 large-v3 权重
│   └── clip_watch.py           # Windows 剪贴板监听
└── .gitignore                  # models/ 3GB 与产物不入库
```

**`fetch_douyin.py` 是完全自包含的** —— 内联了约 50 行 CDP 客户端，不依赖任何外部 skill。

---

## 两个值得记住的坑

**1. 启动 / 抓取 / 关闭必须在同一进程内完成。**
拆成两条命令就会失败：`Popen` 的子进程树会随父进程退出被清理，浏览器瞬间消失。

**2. `collect` 必须带 `--goto` 强制重新导航。**
只是「连接到」一个已加载的页面，捕获不到任何响应 —— 因为 `aweme_detail` 是页面加载后由 XHR 二次发起的。

更多症状见 `references/troubleshooting.md`。

---

## 合规与免责

- 本项目**只读取你自己浏览器正常访问时产生的网络响应**，不伪造签名、不破解加密、不绕过访问控制。
- 请仅用于**个人学习与内容归档**。下载的内容版权属原作者，二次分发或商用请自行取得授权。
- 请遵守抖音的用户协议与 `robots.txt`。高频批量抓取会给对方服务器造成压力，也可能触发风控，**本项目不鼓励也不支持这种行为**。
- 转写文本存在 3–4% 错字率，**不要把它当作逐字准确的引用来源**。
- 使用本项目产生的一切后果由使用者自行承担。

## License

MIT
