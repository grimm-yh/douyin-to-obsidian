# 排错手册

按症状查。每条都来自实测，不是推测。

## 抓取阶段

### 捕获到 0 个 `aweme_detail`

| 可能原因 | 判别方法 | 处理 |
|---|---|---|
| 复用了上次的旧标签页（页面还是上一次的视频） | 日志里「附着标签页」的 URL 与目标 aweme_id 不一致 | 删掉 `--profile` 指向的目录后重跑 |
| 页面还没渲染完 | 日志显示导航成功但立即超时 | `--wait` 加到 25 以上 |
| 视频已删 / 私密 | 页面正文极短 | 无法处理，如实告知用户 |
| 网络异常 | `Network.responseReceived` 事件数为 0 | 检查网络与代理 |

**不要**用只「附着」已加载完页面的方式抓 —— 抖音详情是页面加载后发 XHR 拿的，
必须让页面重新导航一次才会重新请求。

### 调试端口起不来，或起来后几秒消失

根因：**Popen 拉起的子进程树会随父进程退出被清理。**

- ✅ 正确：启动 → 抓取 → 关闭，全在**同一个存活进程**内串行完成
- ❌ 错误：先跑一条命令起浏览器，再跑另一条命令去抓（端口会瞬间消失）

另外两个前置条件：

- Chromium 136+ 必须显式传 `--user-data-dir`，否则直接禁用调试端口
- 必须带 `--remote-allow-origins=*`，否则 websocket 握手被拒

### `Could not copy Chrome cookie database`

浏览器在跑时 `Cookies` 库被独占锁（WinError 32）。
本方案用独立 `--user-data-dir`，不读用户真实 cookie，一般不会遇到。

### 播放地址 403

下载**必须**带请求头：

```bash
ffmpeg -headers 'Referer: https://www.douyin.com/'$'\r\n' \
       -user_agent '<桌面 UA>' -i '<play_url>' -c copy out.mp4
```

漏了 Referer 一律 403。另外地址有时效（`cdn_url_expired`），抓完要尽快下。

## 转写阶段

### `TypeError: open() got an unexpected keyword argument 'metadata_errors'`

PyAV 版本死结：Python 3.14 只能装 `av 19.x`，该版本删了这个参数，
而 faster-whisper 1.2.1 仍在用它。降 `av 14.x` 在新 Python 上没有 wheel。

**解法**：完全绕开 PyAV —— 用 ffmpeg 解码成 float32 numpy 直接喂 `transcribe()`。
`scripts/asr_local.py` 已内置，不要再改回传文件路径。

### `RuntimeError: Library cublas64_12.dll is not found`

装 `nvidia-*` 包**不等于**能用：ctranslate2 不会自动找 DLL 目录。

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-nvrtc-cu12
```

再对 `site-packages/nvidia/{cublas,cudnn,cuda_nvrtc}/bin` 逐个 `os.add_dll_directory()`。
`asr_local.py` 的 `add_cuda_dlls()` 负责这件事。

⚠️ **模型加载成功 ≠ 推理成功。** 用正弦波/静音测会因 VAD 全过滤而「通过」，
必须用真人声测。曾在此处误报成功过一次。

### 转写结果大段重复 / 反复念同一句

Whisper 在长音频上的复读幻觉。必须设 `condition_on_previous_text=False`（已默认）。

### 出现「明镜与点点」「请不吝点赞订阅」等莫名文字

Whisper 训练语料混进了 YouTube 频道片尾语，静音段会吐这些。
`asr_local.py` 的 `HALLUCINATION_MARKERS` 按精确短语清理。
**不要**改成匹配「点赞」「订阅」这类通用词 —— 口播正文本身就会讲到。

### GPU 显存不够

`large-v3` + `float16` 约需 3GB。不足时改 `--device cpu --compute-type int8`，
但 CPU 上 large-v3 慢到不实用，建议换 small/medium 模型。

## 环境与路径

### `OSError: access violation reading 0x0`（读剪贴板）

`GetClipboardData` 返回 64 位 HANDLE，ctypes 默认 restype 是 32 位 `c_int`，
句柄被截断 → `GlobalLock` 拿到空指针。

```python
u.GetClipboardData.argtypes = [w.UINT]
u.GetClipboardData.restype  = w.HANDLE        # 必须
k.GlobalLock.argtypes       = [w.HANDLE]
k.GlobalLock.restype        = ctypes.c_void_p # 必须
```

### `I/O operation on closed file`

模块顶层写 `sys.stdout = io.TextIOWrapper(...)` 会在被 import 时替换调用方的输出流。
改用 `sys.stdout.reconfigure(...)`。

### MSYS 的 `/tmp` 与 Python 的 `C:\tmp` 不是同一位置

Git Bash 里的 `/tmp/foo.wav`，Python 打不开。跨工具传路径用项目内相对路径。

## 操作系统差异

| 项 | Windows | macOS / Linux |
|---|---|---|
| 剪贴板监听 | ✅ `clip_watch.py` | ❌ 不支持（需另实现 pbpaste / xclip） |
| 浏览器路径 | 多候选自动探测 | `/Applications/Google Chrome.app/...` 等 |
| GPU 运行库 | `nvidia-*-cu12` wheel + `add_dll_directory` | 系统级 CUDA，pip 包即可 |
| 优雅关闭实例 | CDP `Browser.close` | 同 |

## B 站抓取

B 站不走浏览器，问题类型和抖音完全不同。**playurl 不需要 WBI 签名**（2026-10 实测），
所以碰到「签名错误」之类的说法基本是别人的旧经验，不适用。

### 只下到 480P，画质比网页上看到的低

| 项 | 说明 |
|---|---|
| 根因 | `playurl` 对未登录请求**静默降级**，返回 `code=0` 但只给 480P，不报错 |
| 判别 | 日志里 `[bili] 画质 690x480`，而网页上明明是 1080P |
| 处理 | 配 `BILI_SESSDATA`。取法：浏览器登录 B 站后，开发者工具 → Application → Cookies → 复制 `SESSDATA` 的值 |

注意 `accept_quality` 字段会列出 1080P+ 等档位，**那是理论支持，不是实际返回**。以实际 `dash.video` 里的轨为准。

### 下载 403

| 项 | 说明 |
|---|---|
| 根因 | 少了 `Referer: https://www.bilibili.com`。B 站 CDN 校验这个头 |
| 判别 | 接口返回正常但下载抛 `HTTPError 403` |
| 处理 | `bili_fetch.py` 的 `_download()` 已内置 Referer + UA。若仍报错，说明改了代码或用了别的工具 |

抖音同理（`Referer: https://www.douyin.com/`），两个平台都吃这一套。

### `没有拿到播放地址，可能需要登录`

按可能性排序：

1. 视频有**区域限制**（只对特定地区开放）
2. 需要登录（部分 UP 主设了关注可见）
3. 视频刚发布，CDN 还没就绪

先试 `BILI_SESSDATA`；仍不行就是区域限制，本项目不处理。

### `该视频只提供单流地址`

老稿件（2018 年前后）只有 `durl` 没有 `dash`，没有分离的音视频轨。
本项目只处理 DASH，遇到这个直接报错。换 `yt-dlp` 能下这种。

### 多 P 视频只处理了一部分

**设计如此**，不是 bug。默认只取第 1P，因为多 P 通常是一整套课程，
一次性跑完既慢又容易把不同主题混进同一篇笔记。

```bash
python scripts/pipeline.py "<链接>" --page 2
```

产物目录会带 `-p2` 后缀，不同 P 不会互相覆盖。

### 转写结果几乎是空的（只有一两段）

| 项 | 说明 |
|---|---|
| 根因 | 纯音乐 / BGM 视频，VAD 把人声以外的全滤掉了 |
| 判别 | `video.txt` 里只有零星一两句，但视频明显有 100 秒以上 |
| 处理 | 正常现象。这类视频的信息在画面上，改开 `--ocr` |

实测一条 107 秒音乐视频：转写只出 1 段 `i want you to know`，推理耗时 1.8 秒（58x 实时）。
不是转写失败，是它本来就没说话。

### 书法 / 艺术字识别不准

实测「吉林」被读成「love 林 a」——因为作者把红色书法笔画叠在字上做了设计。
**这是字体问题，不是分辨率问题**，把 480P 换成 1080P 也救不回来。

印刷体（信息卡、清单、参数表）没这个问题：对照实验里 480P（263 tokens）和 1080P（481 tokens）
对同一张配方卡的识别结果**完全一致**。
