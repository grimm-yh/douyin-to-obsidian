# video-to-obsidian

把视频链接变成结构化 Obsidian 笔记的一条命令流水线。支持**抖音**和**哔哩哔哩**。

```
链接 → 下载 → 转写（B 站有字幕就直接用，否则本地识别）→ 可选识别画面文字 → 生成带 frontmatter 的笔记草稿
```

## 技能作用

| 环节 | 做什么 |
|---|---|
| 下载 | 抖音走隔离浏览器实例抓接口；B 站走公开 API（不需要浏览器）。均自动选最高可用码率 |
| 转写 | B 站约 76% 的视频自带字幕，直接取用，不消耗算力；没有字幕才用本地 faster-whisper large-v3，音频不离开本机 |
| 画面文字 | 可选。识别视频里烧进画面的信息卡（配方克数、参数表、清单），这些内容口播往往不念 |
| 草稿 | 按固定模板生成 Markdown，frontmatter 已填好，直接进 Obsidian 收件箱待归纳 |

## 安装

### 下载安装

```bash
git clone https://github.com/grimm-yh/video-to-obsidian.git
cd video-to-obsidian

pip install faster-whisper numpy websocket-client
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12      # 有 NVIDIA 显卡时装；纯 CPU 可跳过
python scripts/fetch_model.py                         # 下载 large-v3 权重（约 3.1GB）
```

另需系统装有 `ffmpeg`、`ffprobe`，以及一个 Chromium 系浏览器（Chrome / Chromium / Edge 任一，仅抖音需要）。

装完先自检：

```bash
python scripts/pipeline.py --doctor
```

各项显示 `✓` 即可用，缺项按提示补。

### Agent 安装

把仓库整个放到对应 agent 的 skills 目录，agent 靠根目录的 `SKILL.md` 自动发现：

| Agent | 目录 |
|---|---|
| WorkBuddy | `~/.workbuddy/skills/` |
| Claude Code | `~/.claude/skills/` |
| Codex | `~/.codex/skills/` |

```bash
cd ~/.workbuddy/skills
git clone https://github.com/grimm-yh/video-to-obsidian.git
```

放好后新开一个会话，直接把链接发给 agent 即可，不用记命令。

## 使用方式

```bash
# 基本用法
python scripts/pipeline.py "<链接>"

# 加画面文字识别（菜谱、参数、清单类视频建议加）
python scripts/pipeline.py "<链接>" --ocr

# 带领域词，降低专业术语错字
python scripts/pipeline.py "<链接>" --prompt "示例词甲,示例词乙"

# B 站字幕错得离谱时，强制本地转写
python scripts/pipeline.py "<B站链接>" --no-subtitle

# 只下视频不转写
python scripts/pipeline.py "<链接>" --skip-asr

# 批量消费队列
python scripts/pipeline.py --queue queue.jsonl
```

支持的链接形式：

| 平台 | 形式 |
|---|---|
| 抖音 | `https://v.douyin.com/<短码>/`、`https://www.douyin.com/video/<数字ID>` |
| B 站 | `https://www.bilibili.com/video/BV<10位>`、`.../video/av<数字>`、`https://b23.tv/<短码>` |

B 站多 P 视频默认只处理第 1P，用 `--page N` 指定其他。

想让 B 站下 720P/1080P 并**读到字幕列表**（未登录只有 480P 且查不到字幕），跑一次登录脚本：

```bash
python scripts/bili_login.py
```

会弹出一个**独立的**浏览器窗口（不影响你日常用的那个），扫码登录后自动把登录态存进配置。也可以直接粘 `SESSDATA` 到配置文件的 `bili_sessdata`。

产物落在 `<workdir>/downloads/<平台>-<ID>/`，其中 `note_draft.md` 是待归纳的草稿，`overlay_text.json` 是画面文字识别结果（加了 `--ocr` 才有）。

## 需要修改的配置项

全部可选。不配也能跑，只是能力有取舍。

| 配置项 | 环境变量 | 作用 | 不配的后果 |
|---|---|---|---|
| `deepseek_api_key` | `DEEPSEEK_API_KEY` | 画面文字识别 | 不加 `--ocr` 时不会识别画面上的文字卡 |
| `bili_sessdata` | `BILI_SESSDATA` | B 站登录态 | B 站最高只能下 480P，**且查不到字幕列表**（只能本地转写）。用 `bili_login.py` 生成 |
| `browser_exe` | `DY_BROWSER_EXE` | 浏览器路径 | 自动探测常见位置；探测不到则抖音链接无法处理 |
| `model_dir` | `WHISPER_MODEL` | 转写模型目录 | 自动扫描本机已有模型；找不到则需先跑 `fetch_model.py` |
| `vault` | `OBSIDIAN_VAULT` | Obsidian 库根目录 | 仅影响 `--doctor` 的显示，不影响流水线 |
| `port` | `DY_CDP_PORT` | 隔离浏览器调试端口 | 默认 9223，被占用时改 |

配置写在 `~/.video-to-obsidian.json`：

```json
{
  "deepseek_api_key": "<你的密钥>",
  "bili_sessdata": "<你的登录态>",
  "port": 9223
}
```

该文件不随仓库分发，也不会被提交。

## 目录结构

```text
.
├── SKILL.md                    # agent 读取的说明书：适用场景、流程、排错
├── README.md
├── LICENSE
├── references/
│   ├── note-template.md        # frontmatter 字段与排版规范
│   └── troubleshooting.md      # 症状 → 根因 → 处置
└── scripts/
    ├── pipeline.py             # 主入口，串联各环节
    ├── config.py               # 配置解析与自动探测
    ├── fetch_douyin.py         # 抖音：隔离浏览器 + 内联 CDP
    ├── douyin_dl.py            # 抖音：选最高码率并下载
    ├── bili_fetch.py           # B 站：解析链接 + 调 API + 下载合并 + 取字幕
    ├── bili_login.py           # B 站：扫码登录，保存登录态（可选）
    ├── asr_local.py            # 本地转写
    ├── ocr_frames.py           # 画面文字识别
    ├── fetch_model.py          # 下载转写模型
    └── clip_watch.py           # Windows 剪贴板监听（可选入口）
```

## 示例

以下数据全部虚构，仅示意输出形态。

输入：

```bash
python scripts/pipeline.py "https://www.bilibili.com/video/BV1Ab4y1c7De" --ocr
```

终端输出：

```text
[S0] ✓ 模型 / ffmpeg 齐备
[S1] 处理 https://www.bilibili.com/video/BV1Ab4y1c7De
[bili] 示例视频：三步做出番茄炒蛋
[bili] UP 主 示例频道 | 时长 96s
[bili] 画质 1280x720
[bili] ✓ video.mp4  4.2MB
[bili] ✓ 字幕 中文（AI 字幕）210 条 → 48 句，可跳过本地转写
[S3] 用 B 站字幕，跳过本地转写（中文，210 条 → 48 句）
[S3b] [ocr] ✓ 命中 1 处 / 用时 8.1s
  00:12  配料示例 番茄200g 鸡蛋3个
[S4] ✓ 草稿 note_draft.md
[S4]   转写来源: bili-ai
[S4]   画面文字: 1 处已并入草稿
```

生成的 `note_draft.md`：

```markdown
---
标题: 示例视频：三步做出番茄炒蛋
来源: https://www.bilibili.com/video/BV1Ab4y1c7De
作者: 示例频道
创建时间: 2026-01-01
状态: 待复核
标签:
  - 示例标签甲
  - 示例标签乙
---

# 示例视频：三步做出番茄炒蛋

> **一句话结论**
> （待填）

## 一、待填

## 二、待填

## 三、可执行动作

- [ ] 待填

## 四、存疑 / 未验证

- （待填）

## 五、画面文字（视觉识别）

> 以下内容来自画面里的文字卡，**不在口播转写中**。归纳配方/参数时优先用它。

### `00:12`

```text
配料示例
番茄200g
鸡蛋3个
```

## 六、原文转写
（略）
```

## 免责声明

- 本项目只读取浏览器正常访问时产生的网络响应，以及平台公开接口返回的数据，不伪造签名、不破解加密、不绕过访问控制。
- 仅供个人学习与内容归档。下载内容版权归原作者，二次分发或商用请自行取得授权。
- 请遵守各平台用户协议与 `robots.txt`。高频批量抓取会给对方服务器造成压力并可能触发风控，本项目不支持此类用法。
- 转写结果并非逐字准确：本地识别约 3–4% 字错率，平台字幕也会漏字、错同音字。**不要当作逐字准确的引用来源**。
- 开启 `--ocr` 时，视频画面截图会上传至第三方模型接口做识别。含敏感信息（证件、聊天记录、人脸）的视频请自行判断是否开启。
- 使用本项目产生的一切后果由使用者自行承担。
