# 语音功能设计文档（ASR 语音输入 + TTS 语音播报）

> 日期: 2026-08-08
> 状态: 待评审
> 分支: feature/audio
> 关联: bug-115（`clean_text_for_tts` 已实现，本功能复用为 TTS 输入清洗）

---

## 1. 需求概述

在 Gradio Web UI 中新增语音能力：

1. **ASR（语音输入）**：用户点击"开始说话" → 对着麦克风说话 → 实时流式转写文字（边说边出字）→ 静音检测（~1.5-2s）自动结束转写 + 最长 30s 兜底 → 文字填入输入框，用户可改写 → 确认后点「发送」。使用**讯飞语音听写（IAT）** WebSocket API，密钥通过 `.env` 配置。
2. **自定义多音字/热词**：支持配置热词（含拼音标注，API 级）与转写后纠错映射（后处理级），全局 + 项目覆盖两级配置，并输出使用说明。
3. **TTS（语音播报）**：回答生成后自动播报（默认开启），可手动重播。
   - 一期：阿里百炼 `cosyvoice-v3-flash` 模型，默认音色 = 系统音色中的**小男孩**音色（实现时经真实 API 确认 voice id，做成配置项可改），API Key 复用 `DASHSCOPE_API_KEY`（已在 .env）。
   - 二期：`cosyvoice-v3.5-flash` 真人音色定制（设计文档给出操作指引）。
   - 合成流式（SDK 流式回调）+ 播放端缓冲后播放。

---

## 2. 已确认需求清单

| 项 | 确认内容 |
|----|---------|
| ASR 服务 | 讯飞 IAT（语音听写 WebSocket），密钥走 `.env`（APP_ID / API_KEY / API_SECRET） |
| ASR 交互 | 点击开始 → 实时流式转写（边说边出字）→ 静音检测 ~1.5-2s 自动结束 + 30s 兜底 → 填入输入框可改写 → 点发送 |
| 多音字/热词 | 全局 `data/voice/asr_dict.json` + 项目覆盖 `data/voice/{project_id}_asr_dict.json`；热词（含拼音标注）走 API，纠错走后处理 |
| TTS 一期 | 模型 `cosyvoice-v3-flash`，默认音色=小男孩（实现时确认 voice id + 配置项可改），Key 复用 `DASHSCOPE_API_KEY` |
| TTS 二期 | `cosyvoice-v3.5-flash` 真人音色定制（文档给出操作指引） |
| TTS 播放 | 自动播 + 可重播，语音播报默认开启，合成流式 + 缓冲播放 |
| 集成点 | Gradio Web UI（app.py），复用 `clean_text_for_tts` |

---

## 3. 架构

```
浏览器 (Gradio 6.22)
│
├─ [ASR 输入]  gr.Audio(microphone, streaming=True)
│     │  stream 事件（音频块）
│     ▼
│   app.py stream 处理器 ──► src/asr.py（讯飞 IAT WebSocket）
│                                  │  实时部分结果（wpgs 动态修正）
│                                  ▼
│                        msg 输入框实时刷新（可改写）
│
├─ [答案生成]  现有 query/query_stream（不变）
│     │  最终答案
│     ▼  clean_text_for_tts(answer)（复用 bug-115）
├─ [TTS 播报]  respond().then() 链 ──► src/tts.py（cosyvoice-v3-flash 流式合成）
│     │  音频块缓冲 → wav 文件
│     ▼
│   gr.Audio(autoplay=True) 播放（重播=点击播放器）
│
└─ [配置]  src/config.py（.env 新增讯飞 3 键 + TTS 配置）
           data/voice/asr_dict.json（全局多音字/热词）
           data/voice/{project_id}_asr_dict.json（项目覆盖）
```

**模块划分**（遵循现有 src/ 扁平结构约定）：

| 文件 | 职责 |
|------|------|
| `src/asr.py`（新增） | 讯飞 IAT WebSocket 客户端：鉴权、帧组装、热词/纠错、VAD/超时、流式部分结果 |
| `src/tts.py`（新增） | CosyVoice TTS 封装：流式合成回调、长文本分段、wav 输出、二期音色管理工具 |
| `src/config.py`（修改） | 新增讯飞 3 键 + ASR/TTS 配置项 |
| `app.py`（修改） | ASR 录音组件 + 流式转写处理器 + TTS 播报 `.then()` 链 + 开关 |
| `data/voice/*.json`（新增） | 多音字/热词配置（全局 + 项目覆盖） |
| `requirements.txt`（修改） | 新增 `websocket-client>=1.7.0`（本地已装 1.7.0） |
| `.env` / `.env.example`（修改） | 新增讯飞密钥与 TTS 配置注释 |
| `README.md` / `DEPLOY_GUIDE.md`（修改） | 语音功能使用说明、多音字/热词指南、二期音色定制指引 |

---

## 4. 模块设计

### 4.1 `src/asr.py` — 讯飞语音听写（IAT）

**类**：`IflytekASR`

```
IflytekASR(app_id, api_key, api_secret, language="zh_cn", accent="mandarin",
           vad_eos_ms=1800, hotwords=None, corrections=None)
```

**核心方法**：

- `build_auth_url()` → 生成带鉴权的 `wss://iat-api.xfyun.cn/v2/iat` URL
  （HMAC-SHA256 签名：`host/date/request-line` 拼接 → base64；准确实现见下方"协议细节"）
- `feed(audio_pcm: bytes) -> list[dict]` — 发送一帧音频（内部维护 status 0→1），返回该帧后服务端推送的部分结果增量
- `finish() -> str` — 发送尾帧（status=2），等待最终结果，返回完整转写文本
- `close()` — 关闭连接（幂等）
- `correct(text) -> str` — 应用纠错映射（多字符优先、按配置顺序）
- 帧内 `hotwords` 参数：合并全局 + 项目热词（去重、空格分隔、上限 200 个）

**会话管理**：`ASRSessionManager`（app.py 与 asr.py 之间）——每次录音一个会话；首个音频块到达时创建（懒连接），`finish()`/超时/异常时销毁。VAD 由服务端 `vad_eos` 触发（服务端静默后返回最终结果），客户端负责 30s 兜底计时与 `finish()`。

**协议细节（IAT v2 WebSocket）**：

```
Auth:
  signature_origin = f"host: iat-api.xfyun.cn\ndate: {RFC1123-GMT}\nGET /v2/iat HTTP/1.1"
  signature = base64(hmac_sha256(api_secret, signature_origin))
  authorization = base64(f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"')
  url = f"wss://iat-api.xfyun.cn/v2/iat?authorization={authorization}&date={quote(date)}&host={host}"

请求帧（JSON）:
  {
    "common": {"app_id": app_id},
    "business": {
      "language": "zh_cn", "domain": "iat", "accent": "mandarin",
      "vad_eos": 1800,            # 静音检测 ms（服务端判定说话结束）
      "dwa": "wpgs",              # 动态修正：实时返回部分结果（pgs=rpl/apd）
      "hotwords": "司母戊鼎 重庆(chong qing)"
    },
    "data": {"status": 0|1|2, "format": "audio/L16;rate=16000", "encoding": "raw",
             "audio": base64(pcm16k)}
  }

响应:
  {"code":0,"data":{"result":{"ls":bool,"pgs":"rpl|apd","rg":[..],"ws":[{"cw":[{"w":"词"}]}]}}}
  - wpgs 模式：pgs="rpl" 表示按 rg 替换该句部分结果；"apd" 表示追加
  - ls=true 表示该句为最终结果
```

**音频预处理**（`_to_pcm16k(audio_bytes)`）：Gradio 流入的音频块先归一化为 16kHz 单声道 PCM：
- wav 容器 → `wave` 模块剥离头部取 PCM；采样率 ≠ 16k → numpy 线性重采样
- 已为 PCM 则直接使用

**多音字/热词**（`load_dict(project_id)`）：

```json
// data/voice/asr_dict.json（全局）
{
  "hotwords": ["司母戊鼎", "清明上河图", "重庆(chong qing)"],
  "corrections": {"期中": "青铜器", "四亩无顶": "司母戊鼎"}
}
// data/voice/jiabohui_asr_dict.json（项目覆盖，存在时合并覆盖同名热词/纠错）
```

- `hotwords` → 请求帧 `business.hotwords`（空格分隔；拼音标注格式 `词(拼音)` 用于多音字强制读音，实现时用真实 Key 冒烟验证一次该格式，若不起作用则纠错映射兜底）
- `corrections` → `correct()` 后处理（多字符优先替换，保证"司母戊鼎"先于"母鼎"匹配）

### 4.2 `src/tts.py` — CosyVoice TTS

**类**：`CosyVoiceTTS`

```
CosyVoiceTTS(model="cosyvoice-v3-flash", voice="<小男孩音色 id>", format="wav",
             sample_rate=24000, chunk_chars=1000)
```

**核心方法**：

- `synthesize_stream(text, on_chunk: Callable[[bytes], None])` — 流式合成：内部 `SpeechSynthesizer(model=..., voice=..., callback=_ChunkCallback)` + `synth.call(text)`；回调 `on_data(bytes)` 逐块转发给 `on_chunk`；`on_complete`/`on_error` 结束/抛错
- `synthesize_to_file(text, path) -> Path` — 流式合成 + 缓冲写入 wav（供播放端单次交付）
- `split_text(text) -> list[str]` — 长文本按句子边界（`。！？；\n` 等）分段，每段 ≤ `chunk_chars`（cosyvoice 单次合成长度上限防御）
- `ensure_voice(...)` / `list_custom_voices()` — 包装 `VoiceEnrollmentService`（二期音色管理，v1 提供只读工具）

**播放策略（v1）**：合成流式（SDK 回调收集）→ 缓冲完整 wav → `gr.Audio(autoplay=True)` 单次交付播放。"边生成边播"需自定义前端组件（方案 A，用户已否决），v1 不做；cosyvoice-v3-flash 合成远快于实时，额外延迟约 1-2s，可接受。预留 `min_buffer_bytes` 参数便于二期升级。

### 4.3 `src/config.py` 新增配置

```python
# ========== 讯飞语音识别 (ASR) ==========
xfyun_app_id: str = Field(default="", description="讯飞开放平台 APP_ID")
xfyun_api_key: str = Field(default="", description="讯飞开放平台 API_KEY")
xfyun_api_secret: str = Field(default="", description="讯飞开放平台 API_SECRET")
asr_language: str = Field(default="zh_cn", description="识别语言（zh_cn 普通话）")
asr_accent: str = Field(default="mandarin", description="口音（mandarin 普通话）")
asr_vad_eos: int = Field(default=1800, ge=0, description="静音检测时长 ms（VAD 自动结束转写）")
asr_max_duration: int = Field(default=30, ge=1, description="最长录音秒数兜底（超时强制结束）")
asr_sample_rate: int = Field(default=16000, description="IAT 采样率（16k PCM）")
asr_dict_dir: Path = Field(default=Path("data/voice"), description="多音字/热词配置目录")

# ========== 语音合成 (TTS) ==========
tts_enabled: bool = Field(default=True, description="语音播报总开关（默认开）")
tts_model: str = Field(default="cosyvoice-v3-flash", description="TTS 模型（一期；二期真人音色用 cosyvoice-v3.5-flash）")
tts_voice: str = Field(default="", description="TTS 音色（默认小男孩，实现时经 API 确认 id 后填入）")
tts_chunk_chars: int = Field(default=1000, ge=100, description="TTS 长文本分段长度（字符）")
```

`.env` 新增（带注释）：

```
# 讯飞语音听写 (ASR)
XFYUN_APP_ID=your_app_id
XFYUN_API_KEY=your_api_key
XFYUN_API_SECRET=your_api_secret
ASR_VAD_EOS=1800
ASR_MAX_DURATION=30

# 语音合成 (TTS)
TTS_ENABLED=true
TTS_MODEL=cosyvoice-v3-flash
TTS_VOICE=<小男孩音色 id>
```

### 4.4 `app.py` UI 变更

**ASR 输入**：

```
输入行（现有 msg + 发送按钮 之上或下方新增一行）:
  gr.Audio(sources=["microphone"], streaming=True, type="filepath", label="语音输入",
           scale=1) —— 点击开始/停止录音
  voice_status = gr.Markdown("") —— 状态提示（识别中…/已识别完成，可修改后发送/错误）

事件：
  voice_audio.stream →
      asr_stream_handler(audio_chunk_bytes, project_id, voice_status)
      逻辑：首次 chunk → 创建 ASR 会话；后续 chunk → 预处理为 16k PCM → feed()
            → 从队列取部分结果 → correct() → 更新 msg 输入框（实时出字）
            → 服务端 VAD 触发/30s 超时 → finish() → 最终文本写入 msg → 状态"已识别完成"
  voice_audio.stop → 手动停止：finish() + 清理会话
```

**已知限制（Gradio 原生）**：`gr.Audio` 的麦克风录音无法由服务端编程停止（VAD 只能自动结束**转写**，浏览器录音需用户再次点击麦克风停止）。妥协方案：VAD 触发后转写已完成并填入输入框，此后到达的音频块被忽略（用户可边改文字边点麦克风停止录音）。若需"录音也随 VAD 自动停止"，需轻量 JS 桥接（方案 2 录音层，约 30-50 行，二期可加）——**本设计 v1 采用无 JS 妥协方案**，评审时确认。

**TTS 播报**：

```
respond(...) 调用链追加:
  respond(...).then(tts_after_answer, inputs=[chatbot, tts_enabled_checkbox],
                    outputs=[tts_audio, tts_status])
  tts_after_answer(chatbot_history, enabled):
    - enabled=False / 无最后一条 assistant / 无 DASHSCOPE_API_KEY → 跳过
    - 提取最后一条 assistant 正文（按 **[检索来源]** 截断，复用 _convert_history 逻辑）
    - text = clean_text_for_tts(正文)（复用 bug-115）
    - synthesize_to_file(text) → gr.Audio(value=wav, autoplay=True) → 状态"已播报"
    - 异常 → 状态显示错误，不影响回答内容

UI:
  tts_audio = gr.Audio(label="语音播报", autoplay=True)   —— 最新回答音频，可点击重播
  tts_enabled = gr.Checkbox(label="语音播报", value=True) —— 默认开启（用户确认）
```

**流式/非流式均适用**：TTS 在 `respond().then()` 中触发，只对**最终完整答案**合成一次（不做逐 token 合成）。

---

## 5. 数据流

### ASR 数据流

```
用户点击麦克风开始录音
  → Gradio stream 事件逐块送达 (wav chunks)
  → _to_pcm16k() 归一化 16k PCM
  → IflytekASR.feed(pcm)  → 讯飞返回部分结果（wpgs rpl/apd）
  → 部分结果增量合并 → correct() 纠错 → msg 输入框实时刷新
  → 服务端 vad_eos 静默 1.8s 判定说话结束（或 30s 兜底超时）
  → finish() 尾帧 → 最终文本 → msg 输入框（可改写）
  → 用户修改后点击「发送」→ 现有 answer_question 流程（不变）
```

### TTS 数据流

```
answer_question 产出最终答案（流式聚合完成 / 非流式返回）
  → respond().then(tts_after_answer)
  → 提取最后一条 assistant 正文 → clean_text_for_tts()
  → CosyVoiceTTS.synthesize_to_file()（SDK 流式回调 → 缓冲 wav）
  → gr.Audio(value=wav, autoplay=True) 播放
  → 用户可点击播放器重播
```

---

## 6. 错误处理

| 场景 | 处理 |
|------|------|
| 未配置 XFYUN 三键 | ASR 组件禁用 + 状态栏提示"未配置讯飞密钥，请在 .env 补充 XFYUN_APP_ID/API_KEY/API_SECRET" |
| 未配置 DASHSCOPE_API_KEY | TTS 跳过 + 状态"未配置百炼 Key，语音播报不可用"（回答照常显示） |
| 讯飞连接失败/鉴权失败 | 每次录音独立重试 1 次（新建连接），仍失败 → 状态栏报错，不崩溃 |
| 讯飞 API 返回错误码 | 日志记录 code/message，状态栏提示，本次录音丢弃 |
| TTS 合成失败 | 记录日志，状态"语音播报失败"，回答内容不受影响 |
| 长答案超 TTS 单次上限 | `split_text()` 分段顺序合成，逐段拼接 |
| 录音中用户切换项目 | 结束当前 ASR 会话（finish + close），丢弃未完成文本 |

---

## 7. 测试策略（优先 mock，不依赖真实 API Key）

新增测试文件 `tests/test_asr.py`、`tests/test_tts.py`（并入 tests/）：

| 测试类 | 覆盖 |
|--------|------|
| `TestIflytekAuth` | 鉴权 URL 生成（固定 secret 确定性断言）、HMAC 签名正确性 |
| `TestIflytekFrames` | 帧组装 status 0/1/2、base64 编码、hotwords 参数拼接（全局+项目合并、去重、上限） |
| `TestIflytekDict` | 全局/项目覆盖加载、项目文件缺失回退全局、纠错映射多字符优先 |
| `TestIflytekMockWS`（mock websocket） | 部分结果增量合并（wpgs rpl/apd）、ls 最终结果、VAD/超时 finish、异常关闭 |
| `TestPcmPreprocess` | wav→16k PCM 转换（含 48k 重采样）、纯 PCM 直通 |
| `TestCosyVoiceStream`（mock SpeechSynthesizer） | 回调逐块转发的字节流、on_complete/on_error 语义、缓冲写 wav 有效性 |
| `TestTtsSplitting` | 长文本按句边界分段 ≤ chunk_chars、短文本不分段 |
| `TestVoiceUI`（mock pipeline + mock ASR/TTS） | 流式转写处理器实时刷新 msg、VAD 结束填入最终文本、`.then()` 触发 TTS（开关开/关）、无 Key 跳过 |

**回归**：全量 `pytest tests/ -q` 保持 397 passed（2 项已知失败 bug-117b 除外）。

**真实 API 冒烟（实现完成后，用户 Key 已就绪）**：
1. 讯飞：一次真实录音转写，验证热词/拼音标注/纠错生效
2. 百炼：确认 cosyvoice-v3-flash 可用性 + 小男孩音色 id（一次性，写入 TTS_VOICE 默认值）

---

## 8. 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| Gradio 6 `gr.Audio(streaming=True)` 流式输入行为未实测 | 中 | 实现第一步做 spike 验证；不可用则降级方案 2（JS MediaRecorder 录音层，后端 ASR 模块不变） |
| cosyvoice-v3-flash 小男孩音色 id 未知 | 中 | 真实 API 列表/试听确认；`TTS_VOICE` 配置项可随时更换 |
| 讯飞热词拼音标注格式（`词(拼音)`）需验证 | 低 | 真实 Key 冒烟一次；不生效则纠错映射兜底（功能不缺失） |
| VAD 无法自动停止浏览器录音（Gradio 原生限制） | 低 | v1 妥协（转写自动结束、录音由用户点停）；二期可加轻量 JS |
| 音频格式/采样率转换错误 | 低 | `_to_pcm16k` 单测覆盖（wav 48k/16k、纯 PCM） |
| 长答案 TTS 超限 | 低 | `split_text()` 分段 |
| 依赖增加（websocket-client） | 低 | 已装 1.7.0；加入 requirements.txt 即可 |

---

## 9. 二期音色定制操作指引（cosyvoice-v3.5-flash 真人音色）

> 写入 README（v1 交付时文档先行，二期仅需配置切换）。

**原理**：百炼 `VoiceEnrollmentService` 支持一次性上传真人录音样本创建专属音色，之后 TTS 以该音色 id 合成。

**操作步骤**：
1. **准备录音样本**：真人清晰录音（建议 1-2 分钟，16kHz+，无背景噪音，普通话），上传到可匿名访问的 URL（如 OSS/对象存储），格式 wav/mp3，大小 ≤ 10MB。
2. **创建音色**（一次性）：
   ```python
   from dashscope.audio.tts_v2 import VoiceEnrollmentService
   svc = VoiceEnrollmentService()
   voice_id = svc.create_voice(
       target_model="cosyvoice-v3.5-flash",
       prefix="my_voice",                     # 自定义前缀
       url="https://example.com/voice_sample.wav",
       language_hints=["zh"],                 # 语言提示
   )
   print(voice_id)  # 形如 "my_voice_xxx"
   ```
3. **切换配置**（.env）：
   ```
   TTS_MODEL=cosyvoice-v3.5-flash
   TTS_VOICE=my_voice_xxx
   ```
4. **管理音色**：`svc.list_voices(prefix=...)` 查看、`svc.update_voice(voice_id, new_url)` 更新样本、`svc.delete_voice(voice_id)` 删除。
5. 重启 Web UI 即生效。

**注意**：音色样本质量直接决定合成效果；百炼音色定制为有偿服务（按次/按量计费，以控制台为准）。

---

## 10. 多音字/热词使用指南（交付文档要点）

**配置文件**：
- 全局：`data/voice/asr_dict.json`（所有项目共用）
- 项目覆盖：`data/voice/{project_id}_asr_dict.json`（如 `jiabohui_asr_dict.json`，存在时与全局合并）

**热词（提升识别准确率，API 级）**：
```json
{"hotwords": ["司母戊鼎", "小虎", "CosmoVoice", "重庆(chong qing)"]}
```
- 每个热词 ≤ 20 字，最多 200 个；拼音标注格式 `词(拼音)` 用于强制多音字读音
- 适用于：专有名词、领域词、人名、地名、品牌名

**纠错映射（转写后替换，兜底层）**：
```json
{"corrections": {"期中": "青铜器", "四亩无顶": "司母戊鼎"}}
```
- ASR 结果出来后按映射替换（多字符优先）
- 适用于：热词无法覆盖的稳定误识别

**生效时机**：每次 ASR 录音会话开始时重新加载 dict 文件（v1 设计，无需重启服务；实现时确认后再定稿）。

---

## 11. 验收标准

1. 前端点击麦克风开始说话 → 实时流式出字 → 静音 ~2s（或 30s 兜底）自动结束转写 → 文字填入输入框 → 用户改写 → 点发送，问题正常进入问答流程
2. 回答完成后自动语音播报（默认开），可点击重播；关闭"语音播报"开关后不再播报
3. 多音字/热词配置（全局 + 项目覆盖）生效，README 提供使用说明
4. 未配置密钥时功能优雅降级（ASR 禁用提示 / TTS 跳过），不影响问答主流程
5. 全量测试通过（新增 ASR/TTS/UI 测试全部通过，既有 397 passed 不回退）
6. README / .env.example 记录全部新配置项与二期音色定制指引

---

## 附：实现顺序建议

1. `src/config.py` + `.env.example`（配置先行）
2. `src/asr.py`（鉴权/帧/热词/纠错/会话，mock 测试）
3. Gradio 6 Audio streaming spike（验证流式输入行为，决定是否需 JS 降级）
4. `src/tts.py`（流式合成/分段/写 wav，mock 测试）
5. `app.py` 集成（ASR 流式处理器 + `.then()` TTS 链 + UI）
6. 真实 API 冒烟（音色确认、热词拼音验证）
7. 文档（README 使用指南 + 二期指引）+ 全量回归