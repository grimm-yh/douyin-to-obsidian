---
name: douyin-to-obsidian
description: "把抖音视频转成结构化 Obsidian 笔记的完整流水线：链接 → 下载最高码率视频 → 本地 ASR 转写（音频不出机器）→ 可选识别画面文字卡 → 生成带 frontmatter 的笔记草稿。当用户发来抖音链接并希望「整理成笔记 / 存进 Obsidian / 转成文字稿 / 提取口播内容 / 归档这个视频」，或要求批量处理抖音视频、把视频知识点沉淀进知识库时使用。特别注意：作者常把配方克数、参数表、配料清单做成半透明卡片贴在画面上而不念出来，音频转写一定拿不到，需开启画面文字识别。也包含抖音抓取的关键技术细节（CDP 绕过 a_bogus 签名、隔离浏览器实例、剪贴板监听入口）。不做视频剪辑、不做字幕翻译。"
agent_created: true
---

# 抖音视频 → Obsidian 笔记

## 定位

一条命令把抖音链接变成可读的知识点，产物直接落进 Obsidian 收件箱。

```text
抖音链接
  → [S1] 短链解析 + 隔离浏览器实例抓 aweme_detail
  → [S2] ffmpeg 带 Referer 下最高码率
  → [S3] 本地 faster-whisper 转写（音频不出机器）
  → [S3b] 可选：视觉模型识别画面文字卡（--ocr）
  → [S4] 按模板生成 note_draft.md
  → [人工/AI] 归纳正文 → 移入收件箱
```

**边界**：
- ✅ 下载、转写、画面文字识别、生成草稿、结构化排版
- ❌ 视频剪辑、字幕翻译

## 为什么不能更简单（重要，省得重走弯路）

| 想法 | 为什么不行 |
|---|---|
| 用 `yt-dlp` 直接下 | 抖音 `/aweme/v1/web/aweme/detail/` 返回 **403**，根因是未实现 `a_bogus` 签名。**带真登录 cookie 也照样 403** |
| HTTP 直取页面扒 `_ROUTER_DATA` | 返回 72KB HTML 里 `play_addr` 出现 **0 次**、`RENDER_DATA` 不存在 |
| 用无头浏览器 / 伪造签名 | 属于绕过平台访问控制，违反多数平台的 ToS，且随时失效 |

**唯一可行**：用真实浏览器渲染，让页面自己算签名、自己发请求，只读它返回的响应。
等价于人工开 DevTools 看 Network，不触碰风控。

## 安装

源码：<https://github.com/grimm-yh/douyin-to-obsidian>。已装为本地 skill 时可跳过 clone，
直接用 `scripts/` 下的脚本。

```bash
git clone https://github.com/grimm-yh/douyin-to-obsidian.git && cd douyin-to-obsidian
pip install faster-whisper numpy websocket-client
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12      # GPU 必需；纯 CPU 可跳过
python scripts/fetch_model.py                          # 拉 3.08GB large-v3 到 ./models/
```

系统依赖：`ffmpeg`、`ffprobe` 在 PATH 中；一个 Chromium 系浏览器（Chrome / Chromium / Edge）。

画面文字识别另需 **DeepSeek API key**（纯标准库实现，不必装 SDK）：

```bash
export DEEPSEEK_API_KEY="sk-..."
```

## 配置

按优先级：CLI 参数 > 环境变量 > 配置文件 > 自动探测。

```bash
export DY_BROWSER_EXE="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
export WHISPER_MODEL="/path/to/faster-whisper-large-v3"
export OBSIDIAN_VAULT="/path/to/vault"       # 可选
export DY_CDP_PORT=9223                       # 可选，隔离实例端口
export DEEPSEEK_API_KEY="sk-..."              # 可选，画面文字识别
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

先跑诊断确认环境：

```bash
python scripts/pipeline.py --doctor
```

## 用法

```bash
# 完整链路
python scripts/pipeline.py "https://v.douyin.com/XXXX/"

# 带领域词，显著降低专业术语错字（强烈建议）
python scripts/pipeline.py "<链接>" --prompt "AI编程、agent、上下文压缩、私域"

# 加上画面文字识别（配方克数、参数表、步骤清单都靠它）
python scripts/pipeline.py "<链接>" --ocr

# 只要视频，不转写
python scripts/pipeline.py "<链接>" --skip-asr

# 批量：消费剪贴板队列
python scripts/pipeline.py --queue queue.jsonl --ocr
```

单独扫某一段（调试用）：

```bash
python scripts/ocr_frames.py video.mp4 --from 25 --to 45 --keep-frames
```

剪贴板入口（仅 Windows，手机复制链接即可投递）：

```bash
python scripts/clip_watch.py            # 常驻监听，写 queue.jsonl
```

## 输出契约

```text
<workdir>/
├── dy-<aweme_id>.api.json               # 原始接口响应
└── downloads/douyin-<aweme_id>/
    ├── video.mp4                        # 最高码率
    ├── metadata.json                    # 标题/作者/时长/全部码率档
    ├── post_caption.txt                 # 原始文案（含 hashtag）
    ├── video.txt                        # 带 [mm:ss] 时间戳的转写
    ├── video.segments.jsonl             # 分段 {start,end,text}
    ├── overlay_text.json                # 画面文字（--ocr 时产出）
    └── note_draft.md                    # 笔记草稿，frontmatter 已填好
```

`note_draft.md` 的骨架：

```markdown
---
标题: …     来源: …     作者: …
创建时间: …  状态: 待复核
标签: [取自 hashtag，需人工确认]
---

> **一句话结论**
> （待填）

## 一、待填 / 二、待填 / 三、可执行动作 / 四、存疑

## 五、原文转写
> [!note]- 带时间戳全文（点击展开）
```

**S4 之后必须人工/AI 补正文，不要直接入库。** 原因见下。

## 画面文字识别（`--ocr`）

抖音口播视频有个固定套路：**关键数字不念出来，做成半透明卡片打在屏幕上。**
只做 ASR 一定拿不到，而且你不开这个功能永远不知道自己漏了什么。

实测两条视频都命中：

| 视频 | 口播说的是 | 画面卡片写的是 |
|---|---|---|
| 油泼辣子（245s） | 「我一会儿就给您打在屏幕上」 | 细辣椒面 50g / 粗辣椒面 100g / 花椒面 10g |
| 同上 `03:35` | 「再加一小点醋进去」 | 米醋 30g / 糖 1 小勺 |
| HANDOFF（84s） | 整片口播 | 11 处文字卡，等于视频的章节骨架 |

第二行那处尤其典型：**「米醋 30g」在音频里完全不存在**。

### 怎么做的

```text
1fps 抽帧（缩到 720 宽，够认字又省 token）
  → 每 30 帧打包成一次请求（DeepSeek 单请求上限 600 张图）
  → 模型自己挑出「有叠加文字」的帧并逐字抄写
  → 相邻秒、内容相同的合并成一条
```

用 DeepSeek V4.1 Flash（`deepseek-flash`，原生多模态）。纯标准库实现，不依赖 SDK。

### 实测成本

| 视频 | 帧数 | 批次 | 耗时 | tokens |
|---|---|---|---|---|
| 油泼辣子 245s | 245 | 9 | 16.5s | 153K |
| HANDOFF 84s | 84 | 3 | 100s | 56K |

约 **600 tokens/帧**。第二条慢是因为文字卡多、输出长，与帧数无关。

### 两条走不通的路（别重试）

| 想法 | 实际结果 |
|---|---|
| 靠像素差分找「静止叠加层」，只送那几帧 | **失败**。卡片半透明，背景的手和菜透过蒙层显示，该区域帧间差异 28–33，和全片其他部分（中位 33.5）没有区别 |
| 抽帧间隔放到 5 秒以上省 token | **会漏**。`03:35` 那处卡片只存在约 2 秒，15 秒间隔的接触表肉眼也看不出 |

结论：**老老实实 1fps 全片扫**。省下的那点 token 不值得冒漏检的险。

## 已知限制（务必如实告知用户）

| 限制 | 数据 | 应对 |
|---|---|---|
| **真人口播字错率** | **3–4%**（TTS 清晰朗读约 1.4%） | 草稿标 `状态: 待复核`；关键数字与专有名词回看原片 |
| 典型错例 | 「会话结束」→「绘画结束」、「公域」→「公寓」、「1/2」→「1分钟」 | 在笔记「存疑」一节显式列出 |
| **画面文字需显式开启** | 默认关，不加 `--ocr` 就不会识别 | 遇到菜谱/参数/清单类视频**一定加** |
| 画面文字也只覆盖「打在屏幕上」的 | 作者既没念也没打在屏幕上的量（如「白芝麻 多来点」）无从得知 | 按状态判据或适量处理 |
| 网页免费档码率 | 单视频页最高 `normal_1080_0`（1080p / ~680kbps）；**合集页只到 720p** | 别指望高码率原片 |
| 短句破碎 | 抖音口播节奏快，155 段 / 245 秒是常态（约 2.3s 一段） | 归纳时合并同义段，别逐段抄 |
| 地址时效 | 播放地址带 `cdn_url_expired` | 抓完尽快下载，别搁置 |

## 归纳成笔记的规范

排版模板见 `references/note-template.md`。核心几条：

1. 标题下用 `>` 引用块放一句话结论，**只加粗核心句**
2. 章节用中文序号 `## 一、` `## 二、`，每节只讲一件事
3. **对比性信息一律用表格**，不要写成连续 bullet
4. **时间戳不逐条标注**，放在章节标题末尾：`` ## 二、方案　`00:14–00:26` ``
5. 按内容类型选结构：
   - 方法论型 → 问题 / 方案 / 为什么成立 / 落地步骤
   - 操作型（菜谱、教程）→ 配方表 / 关键状态判据 / 分阶段操作 / 收尾
6. 结尾保留 `> [!warning] 质量闸门` 块，写明转写来源、段数、错字情况

## 排错

常见症状与根因见 `references/troubleshooting.md`。最高频的三个：

- **抓到 0 条 `aweme_detail`**：实例复用了上一次的旧标签页 → 删 `--profile` 目录重跑
- **`cublas64_12.dll is not found`**：装了 `nvidia-*` 包但没加 DLL 目录 → 脚本已自动处理；若仍报错，改用 `--device cpu`
- **端口起不来 / 瞬间消失**：启动、抓取、关闭**必须在同一进程内**完成；不要把「起浏览器」和「抓取」拆成两条命令
