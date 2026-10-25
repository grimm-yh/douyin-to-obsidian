---
name: video-to-obsidian
description: "把抖音或 B 站视频转成结构化 Obsidian 笔记的完整流水线：链接 → 下载最高可用画质 → 本地 ASR 转写（音频不出机器）→ 可选识别画面文字卡 → 生成带 frontmatter 的笔记草稿。当用户发来抖音/B 站链接并希望「整理成笔记 / 存进 Obsidian / 转成文字稿 / 提取口播内容 / 归档这个视频」，或要求批量处理视频、把视频知识点沉淀进知识库时使用。特别注意：作者常把配方克数、参数表、配料清单做成半透明卡片贴在画面上而不念出来，音频转写一定拿不到，需开启画面文字识别。也包含两个平台的抓取细节（抖音需 CDP 绕过 a_bogus 签名、B 站走公开 API 无需浏览器）。不做视频剪辑、不做字幕翻译。"
agent_created: true
---

# 视频 → Obsidian 笔记

## 定位

一条命令把**抖音 / B 站**链接变成可读的知识点，产物直接落进 Obsidian 收件箱。

```text
链接
  → [S1] 平台分派
        抖音：短链解析 + 隔离浏览器实例抓 aweme_detail
        B 站：短链跟随 + 公开 API 取 DASH 地址（不需要浏览器）
  → [S2] 下载最高可用画质
        抖音：ffmpeg 带 Referer 直下
        B 站：分别下视频轨/音频轨，ffmpeg 合并
  → [S3] 本地 faster-whisper 转写（音频不出机器）
  → [S3b] 可选：视觉模型识别画面文字卡（--ocr）
  → [S4] 按模板生成 note_draft.md
  → [人工/AI] 归纳正文 → 移入收件箱
```

**边界**：
- ✅ 下载、转写、画面文字识别、生成草稿、结构化排版
- ❌ 视频剪辑、字幕翻译、番剧/直播/课程类链接

## 为什么不能更简单（重要，省得重走弯路）

| 想法 | 为什么不行 |
|---|---|
| 用 `yt-dlp` 直接下 | 抖音 `/aweme/v1/web/aweme/detail/` 返回 **403**，根因是未实现 `a_bogus` 签名。**带真登录 cookie 也照样 403** |
| HTTP 直取页面扒 `_ROUTER_DATA` | 返回 72KB HTML 里 `play_addr` 出现 **0 次**、`RENDER_DATA` 不存在 |
| 用无头浏览器 / 伪造签名 | 属于绕过平台访问控制，违反多数平台的 ToS，且随时失效 |

**唯一可行**：用真实浏览器渲染，让页面自己算签名、自己发请求，只读它返回的响应。
等价于人工开 DevTools 看 Network，不触碰风控。

## B 站与抖音根本不同（别照搬抖音的思路）

| 维度 | 抖音 | B 站 |
|---|---|---|
| 抓取方式 | **必须**真实浏览器 | 公开 API，纯 HTTP |
| 需要浏览器吗 | 是 | **不需要** |
| 签名机制 | `a_bogus`，只能让页面自己算 | `playurl` **实测免 WBI 签名**（2026-10） |
| 防盗链 | 需 `Referer: https://www.douyin.com/` | 需 `Referer: https://www.bilibili.com` |
| 流格式 | 单文件 mp4 | DASH 音视频**分离**，需 ffmpeg 合并 |
| 未登录能拿到 | 1080P（单视频页） | **只有 480P** |
| 带登录态 | 无差别 | 720P / 1080P（大会员还有 1080P+/4K） |

所以 B 站不需要 CDP、不需要浏览器、不需要隔离 profile，一条 HTTP 请求链就够。

### 支持的链接形式

| 形式 | 处理 |
|---|---|
| `https://www.bilibili.com/video/BV<10位>` | 直接取 BV 号 |
| `https://www.bilibili.com/video/av<数字>` | av 号本地转 BV（公开算法，不发请求） |
| `https://b23.tv/<短码>` | 跟随 302 拿真实地址 |
| `.../video/BV1xx?p=3` | 识别分 P，取第 3P |
| `.../video/BV1xx?t=120&spm_id_from=...` | 忽略无关参数 |

**不支持**（会明确报错说明，不会静默失败）：

| 类型 | 判定方式 |
|---|---|
| 番剧 `bilibili.com/bangumi/` | 路径前缀匹配，直接拒 |
| 直播 `live.bilibili.com` | 同上 |
| 课程 `bilibili.com/cheese/` | 同上 |
| 动态 `bilibili.com/opus/` | 同上 |
| 专栏 `bilibili.com/read/` | 同上 |
| 用户主页 `space.bilibili.com` | 同上 |
| 收藏夹 `bilibili.com/medialist/`、`/list/` | 同上 |
| 音频 `bilibili.com/audio/` | 同上 |
| 任意非 B 站链接 | 正则抠不到 BV/av，报「找不到 BV 号」 |

### 识别流程（bili_fetch.py）

```text
1. resolve_bvid()   短链跟 302 → 正则抠 BV 或 av → av 本地转 BV
2. view 接口        标题 / UP主 / 时长 / 分P 列表 / cid
3. playurl 接口     DASH 视频轨 + 音频轨（免签名，qn=116 fnval=4048）
4. pick_quality()   同分辨率优先 avc，再按码率
5. download()       分别下 video/audio 到临时文件 → ffmpeg -c copy 合并
6. metadata 字段对齐抖音（aweme_id 填 bvid），S4 生成草稿无需平台分支
```

### B 站侧失败情况

| 现象 | 原因 | 报错与处置 |
|---|---|---|
| `code=-404` | 视频不存在或已删除 | 明确告知，不再重试 |
| `code=-403` | 私密视频 | 告知无权限 |
| `code=62002` | 充电专属 | 提示需大会员/充电 |
| `code=-400` | BV 号格式对但无此视频 | 告知并给出支持的格式 |
| `durl` 而不是 `dash` | 老视频只提供单流 | 明确说不支持，建议换工具 |
| 没有 dash 且无 durl | 需登录 / 有区域限制 | 提示设 `BILI_SESSDATA` |
| 只有 480P | 未登录，playurl 静默降级 | **不报错**，日志里打印实际画质 |
| 下载 403 | 少了 Referer | 代码内置，正常不会发生 |
| 分 P 序号越界 | `--page` 超过总 P 数 | 列出总 P 数并拒绝 |

设计原则：**能明确说清原因的一律抛 `BiliError` 带人话，绝不让队列静默中断**。
pipeline 侧用 `try/except Exception` 兜底，单条失败不影响队列其他条目。

### B 站实测数据（2026-10-25）

| 项 | 值 |
|---|---|
| 公开视频 108s | 未登录 690x480 / 51kbps / 2.0MB / 下载 <1s |
| 音视频合并 | ffmpeg `-c copy`，无损无重编码 |
| 480P 下识别印刷体信息卡 | **对照实验：1080P 用 481 tokens 全对，480P 用 263 tokens 也全对** |
| 480P 下识别书法艺术字 | **不可靠**。「吉林」被读成「love 林 a」（红色装饰笔画干扰） |

结论：**未登录的 480P 对印刷体信息卡够用**；`BILI_SESSDATA` 的价值在于画面更清晰、以及能看大会员专属内容，不是 OCR 的前置条件。
书法/手写体是字体问题，提分辨率也救不回来。

## 安装

源码：<https://github.com/grimm-yh/video-to-obsidian>。已装为本地 skill 时可跳过 clone，
直接用 `scripts/` 下的脚本。

```bash
git clone https://github.com/grimm-yh/video-to-obsidian.git && cd video-to-obsidian
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
export BILI_SESSDATA="..."                    # 可选，B 站高清
```

或写 `~/.video-to-obsidian.json`：

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
# 抖音
python scripts/pipeline.py "https://v.douyin.com/XXXX/"

# B 站（不需要浏览器，也不会启动隔离实例）
python scripts/pipeline.py "https://www.bilibili.com/video/BV1xxxxxxxxx"

# 带领域词，显著降低专业术语错字（强烈建议）
python scripts/pipeline.py "<链接>" --prompt "AI编程、agent、上下文压缩、私域"

# 加上画面文字识别（配方克数、参数表、步骤清单都靠它）
python scripts/pipeline.py "<链接>" --ocr

# B 站多 P 视频取第 3P
python scripts/pipeline.py "<B站链接>" --page 3

# 只要视频，不转写
python scripts/pipeline.py "<链接>" --skip-asr

# 批量：消费剪贴板队列（两个平台的链接可以混在同一个队列里）
python scripts/pipeline.py --queue queue.jsonl --ocr
```

只处理 B 站链接时，前置检查不会要求浏览器 —— 没装 Chromium 也能跑。

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
├── dy-<aweme_id>.api.json               # 抖音原始接口响应
└── downloads/
    ├── douyin-<aweme_id>/               # 抖音
    │   ├── video.mp4                    # 最高码率
    │   ├── metadata.json                # 标题/作者/时长/全部码率档
    │   ├── post_caption.txt             # 原始文案（含 hashtag）
    │   ├── video.txt                    # 带 [mm:ss] 时间戳的转写
    │   ├── video.segments.jsonl         # 分段 {start,end,text}
    │   ├── overlay_text.json            # 画面文字（--ocr 时产出）
    │   └── note_draft.md                # 笔记草稿
    └── bili-<bvid>[-p<N>]/              # B 站（多 P 时带 -p 后缀）
        ├── video.mp4                    # 音视频已合并
        ├── metadata.json                # 字段与抖音对齐，额外含 bvid/cid/page
        ├── post_caption.txt
        ├── video.txt / video.segments.jsonl
        ├── overlay_text.json
        └── note_draft.md
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
| **B 站未登录只有 480P** | playurl 静默降级，不报错，只在日志里显示实际画质 | 印刷体信息卡在 480P 下仍能认全；要更高画质配 `BILI_SESSDATA` |
| **音乐/纯 BGM 视频转写几乎为空** | VAD 会滤掉非人声，可能只出 1 段 | 正常现象，不是失败。看 `video.txt` 确认 |
| **书法/艺术字识别不可靠** | 「吉林」被读成「love 林 a」，红色装饰笔画干扰 | 提分辨率救不回来，属字体问题 |
| 网页免费档码率 | 单视频页最高 `normal_1080_0`（1080p / ~680kbps）；**合集页只到 720p** | 别指望高码率原片 |
| 短句破碎 | 抖音口播节奏快，155 段 / 245 秒是常态（约 2.3s 一段） | 归纳时合并同义段，别逐段抄 |
| 地址时效 | 两个平台的播放地址都带过期参数 | 抓完尽快下载，别搁置 |

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

常见症状与根因见 `references/troubleshooting.md`。最高频的几个：

- **抓到 0 条 `aweme_detail`**：实例复用了上一次的旧标签页 → 删 `--profile` 目录重跑
- **`cublas64_12.dll is not found`**：装了 `nvidia-*` 包但没加 DLL 目录 → 脚本已自动处理；若仍报错，改用 `--device cpu`
- **端口起不来 / 瞬间消失**：启动、抓取、关闭**必须在同一进程内**完成；不要把「起浏览器」和「抓取」拆成两条命令

**B 站侧**

- **只下到 480P**：未登录的正常表现，不是 bug。要高清就配 `BILI_SESSDATA`
- **下载 403**：少了 `Referer: https://www.bilibili.com`。`bili_fetch.py` 已内置，出现这错说明用了别的工具或改了代码
- **没有拿到播放地址**：可能是有区域限制的视频，或需要登录。先试配 `BILI_SESSDATA`
- **该视频只提供单流地址**：老稿件只有 `durl` 没有 `dash`，本项目不处理，换 `yt-dlp` 或别的工具
- **多 P 视频只处理了一部分**：默认只取第 1P，用 `--page N` 逐 P 跑
- **日志里画质比预期低**：playurl 静默降级，不会报错。看 `[bili] 画质 ...` 那行确认实际值

**两个平台共通**

- **无法识别的链接**：只支持抖音和 B 站。检查链接里是否含 `douyin.com` / `bilibili.com` / `b23.tv`
- **转写几乎是空的**：多半是纯音乐视频，VAD 把人声以外的部分全滤掉了，属正常，不是失败
