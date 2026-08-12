# 文化知识库 RAG 问答系统

> 基于阿里云百炼（DashScope）API 的端到端知识库检索增强生成（RAG）系统。
> 支持**多项目架构**，每个项目独立配置、独立向量集合、独立 Prompt 模板，适用于任意领域（博物馆、企业、法律、医疗等）。
> 支持推荐、事实查询、比较分析、开放讨论等多种问答类型。

---

## 📋 目录

- [项目概述](#项目概述)
- [更新日志](#更新日志)
- [系统架构](#系统架构)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [使用指南](#使用指南)
- [数据处理流程](#数据处理流程)
- [RAG 问答流程](#rag-问答流程)
- [多项目架构](#多项目架构)
- [多格式文档支持](#多格式文档支持)
- [API 参考](#api-参考)
- [Conda 环境部署（GPU 服务器）](#conda-环境部署gpu-服务器)
- [部署指南](#部署指南)
- [性能优化](#性能优化)
- [Web UI 问答界面](#web-ui-问答界面)
- [项目结构](#项目结构)
- [常见问题](#常见问题)

---

## 更新日志

### v1.5.0 (2026-08-12) — 语音助手：唤醒 + VAD + 双计时 + 打断（第十四轮 audit-ASR）

> 语音输入从「点击录音」升级为**免提语音助手**（默认关闭，`VOICE_ASSIST_ENABLED=true` 开启，
> 关闭时手动模式行为零变化）：唤醒词唤醒 → 双计时自动提问 → 播报可打断 → 多轮循环。
> 真实 API 全链路冒烟实证（`scripts/smoke_voice_assist.py`）：唤醒命中 → 进入 8s 提问窗 →
> 提问识别 wpgs 边说边出字（说完时首字早已在屏上，<1s 达成）→ 2s 静默自动提交。

**六项能力**（需求 → 方案）：

1. **自动唤醒**：唤醒词可编辑（`.env ASR_WAKE_WORDS` 逗号分隔；`asr_dict.json` 的
   `wake_words`/`wake_greeting` 项目级覆盖）。待机态语音段经 VAD 门控才送讯飞
   （省额度），识别文本**先归一（去标点）再纠错**后子串匹配唤醒词（"泥好，小胡！"也能命中）。
   应答语默认「您好，我是小虎，请问有什么可以帮您？」——**合成一次内存+磁盘缓存复用**
   （`data/processed/tts_cache/greeting_<hash>.wav`，零合成延迟）。
2. **前置 VAD**：silero-vad（模型内置 `src/assets/silero_vad.onnx`，MIT 许可；自持 ONNX
   推理，**不装 silero-vad 包**——其顶层依赖 torchaudio）。参数按用户标定：
   `VAD_THRESHOLD=0.5 / VAD_MIN_SPEECH_MS=400`（过滤"嗯/啊"）
   `/ VAD_MIN_SILENCE_MS=800`（段结束）`/ VAD_SPEECH_PAD_MS=200` / `VAD_MAX_SPEECH_S=15`
   （强制切段防卡死）。关键实证：官方推理每窗需前拼 64 采样上下文，缺失则概率输出全废。
3. **双计时**：播报结束进倾听态，8s 内无语音→回待机（流程中断）；每段语音结束延长 2s，
   循环延长；2s 静默→视为提问完毕→**自动提交问答**（隐藏 Textbox `.change` 独立事件——
   gradio 单事件 concurrency_limit=1，塞进流事件会堵死打断检测）。
4. **多轮 + 打断**：播报中 VAD 确认持续语音（≥400ms）即打断——服务端按 `session_hash`
   定位当前播报 token，respond 每 0.1s 拍检查 cancel（停喂停发、取消 TTS 会话、跳过收尾）；
   客户端 60s HLS 缓冲由 head JS 观察 `voice_status` 的 ⚡ 标记**强停 `<video>`**。
   打断的语音直接作为新问题（免唤醒）。手动发送的回答同样注册 token、可被打断。
5. **识别提速**：保持讯飞流式（wpgs 边说边出字，实测增量 <500ms）——"说完到首字 <1s"
   由流式天然满足；旧「客户端静音块判停 2s」被 VAD 800ms 端点取代（仅助手模式）。
   决策记录：否决"说完再一次性识别"（整段重识别必超 1s）。
6. **纠词典新格式**：`asr_dict.json` 支持顶层 `[{"from":"巨声智能","to":"具身智能"}]`
   列表（与旧 dict 形态兼容，可混用；全局+项目级合并，多字符优先替换照旧）。

**防自触发（一体机外放关键）**：新增 `patch_gradio_mic_aec()`——gradio 录音默认
`getUserMedia({audio:true})` 无 AEC，TTS 播报会被自己麦克风拾取 → VAD 误判 → 死循环；
补丁强制 `echoCancellation/noiseSuppression/autoGainControl`（浏览器 AEC 以本页输出为参考）。
叠加双保险：播报期间只跑 VAD 不送 ASR。`verify_frontend_patches()` 自检扩为 3+1 标记。

**免提常驻收音**：head JS 页面加载自动点录音（`VOICE_ASSIST_ENABLED` 才注入）；
一体机无人值守用 Chrome `--use-fake-ui-for-media-stream` 免授权弹窗（落地方案 §5.5-7 惯例）。

**新增文件**：`src/vad.py`（StreamVAD 分段状态机 + SileroVadOnnx 自持推理）、
`src/voice_assistant.py`（四态状态机 standby/await_broadcast/broadcast/listen，纯逻辑零 gradio 依赖）、
`src/assets/silero_vad.onnx`、`tests/test_voice_assist.py`（52 项，全离线）、
`tests/fixtures/vad_*.wav`（TTS 预生成中文语音夹具）、`scripts/smoke_voice_assist.py`。
**依赖**：新增 `onnxruntime>=1.16.0`（纯 CPU 足够，0.095ms/30ms 窗）。
**全量测试：615 passed**（基线 563）。

### v1.4.0 (2026-08-12) — TTS 播报架构重做（第十三轮 audit-TTS）

> 语音播报架构重做——**单会话流式合成**。五项验收全过：首播 TTS 侧 ~1.0s（≤1s）、
> 全程零断流、第 2 轮起正常播报、音质与整段合成一致（单编码器连续 AAC 流）、断句连贯
> （句边界批量喂 + 静默压缩）。语速 1.1x（`TTS_SPEECH_RATE` 可调）。
> 物理下限 ≈1.1s：API 首块 0.6s + 转码 0.23s + 客户端启动 ~0.4s。
> 全程零停顿（真实浏览器两轮 E2E 实证）、第 2 轮起播报恢复。详见 `code_review_report_v3.md` 第九节。
>
> 诊断/复现脚本（`scripts/`）：`e2e_tts_browser.py`（真实浏览器双轮 E2E，--mock-llm 隔离
> LLM 波动）、`tts_starve_probe.py`（断粮/分块烙停顿实验）、`verify_stall_beacon.py`
> （客户端停顿遥测链路验证）、`measure_tts_*.py`、`tts_stall_sim.py`、`repro_hls_rounds.py`。
> 生产观测日志：`TTS 首播/播报收尾`、`TTS 缓冲告急`、`播报静默审计`、`客户端停顿上报`。

#### TTS 播报重做（首句延迟 + 中途停顿 + 多轮无声）

**根因链（全部 E2E/源码实证，非推测）**：

1. **首播 3s 的真相**：分段独立合成每段等整段完成（~2s）+ 首播攒批门（5 chunk + 2s 等待）。
   实测 dashscope 流式**首音频块仅 ~0.6s 且与文本长度无关** → 改为每个回答**一个流式会话**，
   LLM 文本增量喂入（首段 8 字即喂），PCM 音频边产边播（爬坡批次 0.4/0.6/0.8s→0.9s）。
2. **中途停顿的真相（多层叠加）**：
   - `streaming_complete()` **阻塞等全部合成完成**（实测 26s+）且完成后 close 连接——同步调用
     冻结发布（E2E 实证 playlist 冻结 22s）→ 改后台线程（真凶之一）
   - 音频收集耦联在 LLM yield 上，LLM 流停顿（高峰实测 40s+）即断流 → answer_question 改
     后台泵线程，音频按 0.1s 节拍独立发布
   - 前端 patch 只修第 2 轮无声，但**每个音频批 yield 都重建 hls 实例**（MediaSource 销毁重载，
     E2E 实证）→ ke() 按 URL 去重：同流复用、新轮才重建
   - `lowLatencyMode:true` 让播放器贴 live edge（前向缓冲恒 ~0.1s，实证）→ 关闭；
     `maxBufferLength:1`→60s 深缓冲
   - gradio 服务端 `MediaStream.max_duration` 每段 +1 蠕变 → TARGETDURATION 膨胀，hls.js 无更新
     时按 TD/2 轮询（仿真 22.5s 停顿）→ 修正为 clamp(ceil(段时长),1,5)（≤1s 批次下 TD=1，规范内）
   - 转码 pydub 双进程 0.7s/批（Windows）发布吞吐 0.7x 实时必断流（实测 146.7s 音频发布 269.7s）
     → 单 ffmpeg 进程 0.23s/批；**EXTINF 改报真实解码时长**（AAC priming 2s→2.048s，原声明漂移
     48ms/段致 MSE 空洞、播放器卡固定位置，实证）
3. **第 2 轮起无声的真相**：服务端无误（多轮 playlist 正常）；前端 patch 未覆盖原生 HLS 分支
   （Safari/无 MSE）+ **patch 原地改文件、哈希文件名不变，浏览器启发式缓存让客户端长期跑旧 JS**
   → 原生分支按 URL 去重重赋值 + `/assets/*.js` no-cache 中间件强制 revalidate。

**音质修复（同轮后续）**：逐批独立编码的每个 AAC 段带 ~43ms priming 头静音 + 拖尾
（0.9s 段即每 0.9s 一个接口吞音，实测段开头 42.7ms 才有声、接口能量塌至 1/3）→
改为**单 ffmpeg 进程持续编码**（PCM 实时写 stdin，stdout 是一条连续 AAC 流，按帧界切片，
`_AdtsStreamer`），接口编码状态连续无缝（实测接口 RMS 与整体一致）；转码 patch 增加
**ADTS 直通**（已是 AAC 的段原样透传，避免二次编码代际损失）；码率显式 `-b:a 96k`；
编码器探针 `-probesize 32 -analyzeduration 0`（原始 PCM 无头可探，默认探针缓冲会攒
~0.7s 才开工，首播回退的根因）；重播文件改走**原始 PCM**（与编码器健康解耦，质量无损）。

**语速与中途停顿加固（第三轮）**：
- **语速 +10%**：`speech_rate=1.1`（`TTS_SPEECH_RATE` 可配），流式/非流式路径均透传
  （真实 API 实测时长比 0.909 = 精确 1/1.1）。
- **中途停顿根因定论**：断粮烙停顿假设被实验**证伪**（`scripts/tts_starve_probe.py`：
  整段喂 / 断粮 2.5s / 仅拆分三组音频逐字节一致）——2-3s 中途停顿是播放器缓冲被 LLM
  出文停顿耗空的**断流**，音频内容无停顿。加固：标准段 0.9s→2.0s（高 RTT 客户端下
  拉段请求频率减半；首播爬坡 0.4/0.6/0.8 不变，TD patch clamp 到 2）+ respond 新增
  **缓冲告急诊断日志**（估算剩余缓冲 <1.5s 且 PCM 断流 >1s 时告警，复测时可据此定位）。
- 若仍有中途停顿，优先确认客户端 HLS patch 生效（启动日志 4 行 + 浏览器硬刷）。
- **客户端停顿遥测**（第四轮）：页面 head 注入探针（`launch(head=)`），自动播报的
  video 元素每次 waiting→playing ≥0.4s 自动 sendBeacon `/__tts_stall`（ASGI 中间件
  `_TtsStallBeaconMiddleware` 直接应答 204 并落 WARNING 日志：停顿时长/播放位置/
  **前向缓冲**——ahead≈0 = 发布/网络追不平；ahead>3 = 播放器侧问题）。配套
  `verify_frontend_patches()` 启动自检：复读磁盘 StaticAudio-*.js 确认三标记落盘。
- **中途停顿根因定论与根治（第五轮）**：遥测零上报 + patch 自检通过 → 停顿不在播放
  侧。真实 API 复现实验定论：**喂入式合成在每个 streaming_call 边界烙入 ~0.9s 静默**
  （整段喂 0 处；20 字块喂 5 处/34s——与用户感知的 4-5 处吻合；此前断粮实验漏网是
  因为只切 2 块且断粮 2.5s 恰好不触发）。修复：`_PauseCompressor` 在 PCM 流上实时
  压缩静默（20ms 窗，峰值 <-40dB 判静默，每段静默保留前 0.35s 自然气口、超出丢弃，
  逐窗判决零额外延迟）——真实 API 验证：同文本 5 处静默 → **0 处**，丢弃 2.9s。
  配套 `_audit_silence` 播报静默审计（每轮收尾后台扫描重播 PCM，≥0.6s 静默即
  WARNING 带位置）作为回归护栏。
- **断句连贯性（第五轮补充）**：压缩机把边界停顿压到 0.35s 仍隐约可闻 → 喂入策略
  改为**句边界批量喂**（`_take_first_unit`/`_take_feed_unit`）：首播单元句末优先、
  ≥8 字逗号兜底（保首播速度）；后续攒 ≥60 字（`TTS_ACCUM_CHARS` 默认 20→60）完整句
  才喂、只在句末标点切，断粮 >2.5s 有完整句即喂——200 字回答喂入次数 12→4，边界
  全部落在自然停顿处。压缩机降为兜底。首播 TTS 侧 ~1.0s 持平。
- **数字区间“-”不念**（用户实测）：`clean_text_for_tts` 新增 `_convert_dash_ranges`
  ——数字间 - / – / — 转“到”（3月18–21日 → 3月18到21日、9:00-17:00 → 9:00到17:00），
  ISO 日期转中文日期（2026-08-12 → 2026年8月12日），非数字连字符不动。

**改动文件**：`app.py`（respond 重做：泵线程 + 流式会话 + `_AdtsStreamer` 持续编码 +
看门狗重建 ≤2 次；`_NoCacheAssetsMiddleware`；缓冲告急诊断）、`src/tts.py`
（`CosyVoiceTTS.start_stream`/`_StreamHandle`，PCM_24000 格式；`speech_rate` 透传）、
`src/audio_bootstrap.py`（HLS patch 扩展 + TD 修正 + 转码提速/ADTS 直通三个 monkeypatch）、
`src/config.py`（新增 `TTS_SPEECH_RATE/TTS_FIRST_FRAGMENT_CHARS/TTS_BATCH_SECONDS/
TTS_FIRST_BATCH_SECONDS/TTS_STREAM_WATCHDOG_SECONDS`，弃用 `TTS_FIRST_BATCH_BLOCKS`）。
**顺带修复 bug-123**：answer_question 错误路径给 gr.JSON 喂空串 → 整个事件静默失败（用户连
错误提示都看不到），改 gr.update()/"[]"。
**新增测试 27 个**（`tests/test_tts_broadcast.py`，全离线：流式会话/批次器/看门狗/前端 patch/
中间件/TD 修正/转码/停顿仿真）+ `scripts/e2e_tts_browser.py`（真实浏览器验收，含 mock-LLM 隔离模式）。
生产可观测：启动日志可见 4 个 patch 状态；`TTS 首播:` 日志（喂文本→发布秒数）；`TTS 播报收尾:`
（批次/音频/耗时/重建次数）；`TTS 音频块间隔异常`（API 侧断流 >3s 告警）。
**残余风险**：LLM 流中段长停顿（内容缺口）下播报必然中断后自动恢复——缓冲可吸收 <20s 波动；
dashscope 侧长时间断流由看门狗 15s 重建兜底（重喂最后片段，有界重复 ≤1 段）。

---

### v1.3.5-pre (开发中) — 新增功能与修复（第九/十/十一/十二轮）

#### 新增功能
- **语音输入与播报（bug-121）**：Web UI 新增语音功能——
  - **ASR 语音输入**：讯飞语音听写（IAT v2）WebSocket 流式转写，点击麦克风开始说话，**边说边出字**；
    说完静默约 2 秒自动结束（服务端 VAD），最长 30 秒兜底；识别文字自动填入输入框，可改写后发送；
    支持自定义多音字/热词（`data/voice/asr_dict.json` 全局 + `data/voice/{project_id}_asr_dict.json` 项目覆盖）
  - **TTS 语音播报**：阿里百炼 `cosyvoice-v3-flash`（默认音色 `longanyang` 小男孩），**句子级流式播放**
    （第一句合成完即播，逐句无缝续播），回答完成后自动播报 + 可点击重播；
    “语音播报”开关默认开启；二期可定制真人音色（`cosyvoice-v3.5-flash` 音色库，见使用指南）
  - 密钥与模型均走 `.env` 配置（`XFYUN_APP_ID/XFYUN_API_KEY/XFYUN_API_SECRET`、`DASHSCOPE_API_KEY`、`TTS_MODEL/TTS_VOICE`）
- **Excel (.xlsx) 数据源支持（bug-109）**：表格型 Excel 可直接作为知识库数据源（每行一条记录、多 sheet 支持、任意列可检索）；docs 模式自动识别 + json 模式 `--json-path xxx.xlsx` 双入口；openpyxl 可选依赖
- **Embedding 模型升级 text-embedding-v3 → v4（bug-110）**：默认模型升级，API 契约/批大小上限/维度不变；`.env.example` 顺带修正键名拼写 `EMBEDDING_MOD_NAME` → `EMBEDDING_MODEL_NAME`
- **意图理解分层分类（bug-113）**：用户意图理解从纯规则升级为工业界主流的**分层级联**——
  L0 规则闲聊路由（`is_kb_related`，零成本，保留）→ L1 向量语义分类（`SemanticIntentClassifier`，
  5 类意图原型相似度，复用 Embedding 缓存，语义泛化）→ L2 LLM 兜底（`classify_with_llm`，
  仅 L1 低置信度时调用，按次计费）→ 规则评分保住底；L1/L2 识别出规则层漏掉的闲聊自动转闲聊分支；
  配置：`INTENT_SEMANTIC_ENABLED` / `INTENT_SEMANTIC_THRESHOLD`(0.50) / `INTENT_LLM_FALLBACK_ENABLED`
  **首字延迟优化**：原型向量 `embed_batch` 批量预计算（首次 9.5s → 0.6s，启动 warmup 完成，首查不阻塞）、
  持久化于 pattern_cache（重启零成本）；高置信/闲聊首字与旧版持平（实测 ~550-740ms），
  低置信问题 L2 LLM 为准确率代价（~800ms，无法并行消除）
- **输出答案去除 emoji（bug-114）**：LLM 回答统一过滤 emoji/装饰图标（`strip_emoji`，
  覆盖表情/交通/符号/几何/箭头等 Unicode 范围，不误伤中文标点与 ©→ 等普通符号）；
  UI 检索来源/状态/按钮的 14 处图标全部改为纯文本（`**[检索来源]**`、`[高]/[中]/[低]` 相关度标记）


#### Bug 修复
- **Web UI 项目下拉框误切换（bug-111，P0）**：下拉框 choices/value 硬编码导致 `--project jiabohui` 启动后页面加载把全局 pipeline 误切换成 museum；改为 choices 动态来自 ProjectManager、value 跟随 `--project` 参数
- **推荐类回答混入不相关项（bug-112，P1）**：recommend prompt 增加相关性优先 + 品类匹配指令（"不相关的项不要推荐，宁缺毋滥"）；根因更正：服务器重排实际用 qwen3-rerank API（生效），非 TF-IDF 降级

#### 全面代码审查 + 修复（第十二轮，audit-F1~F27，P0×5 + P1×7 + P2×11）

> 测试工程师视角从零审查（不假设任何代码正确），疑点先最小复现再固化为测试，
> 完整报告见 `code_review_report_v3.md`（最新有效审查文档）。

- **P0 Excel 首行空行整表静默丢失（audit-F1）**：`_load_xlsx` 表头识别改 None 标志位 + 跳过前导空行
- **P0 ASR 编码容器魔数 3 处错误（audit-F2）**：`ftyp` 实际在偏移 4、`ID3`(3字节)/`\xff\xfb`(2字节)与 4 字节切片比较永不命中 → Safari 录音(mp4/m4a)/mp3 被当裸 PCM 识别乱码；按魔数实际位置/长度比较
- **P0 ASR 异常帧杀死接收线程（audit-F3）**：`w["cw"][0]["w"]` 遇空 cw 抛 IndexError 静默杀线程；逐词防御解析 + 接收循环异常保护，`ls` 终帧独立判断
- **P0 检索瞬时故障结果被缓存 5 分钟（audit-F4）**：任一侧检索失败不写 `retrieval_cache`
- **P0 rerank 单候选 RRF 分被当 0~1 相关性分（audit-F5）**：相关性闸门（阈值 0.45）必误判；单候选也走 API 拿真实相关性分
- **P1 鲁棒性防御（audit-F6/F7/F8/F17）**：单点 `metadata_json` 损坏不再杀死整个语义检索；`VectorStore.close()` 后访问 client 抛清晰 RuntimeError（原静默 None）；`batch_size<1` 钳制；本地重排空词表崩溃不再穿透降级保护
- **P1 文档控制字符清洗落地（audit-F9）**：`load_file` 统一清洗 C0 控制字符（保留 \n\t\r），bug-117b 长期失败的 2 个测试转绿（并修正了其中自相矛盾的断言）
- **P1 展示/映射（audit-F10/F11/F12）**：RRF 量纲阈值收紧至 0.02（理论上限 1/61≈0.0164），重排低分不再误标[高]；`_select_prompt` 补 CHITCHAT 映射；TTS 重播文件按请求唯一命名（多用户不再互相覆写）
- **P2 一批**：`to_text` 非字符串 tags、货币$与LaTeX$配对错乱、"谢谢你"判闲聊、`.env` 键名拼写 `EMBEDDING_MOD_NAME`→`EMBEDDING_MODEL_NAME`、`.ppt` 友好降级、外部项目 id 启动校验（防路径穿越）、`show_error` 仅 DEBUG、LLM 意图否定表述不误判、问候词边界匹配（"存在吗"不误伤）、缓存原子写
- **评估后维持原决策（2 项）**：否定句命中模式缓存（bug-006 既定决策）、ASR finalized 后忽略后续块（TestAsrGuards 防无限识别既定防护），代码注释记录依据

**全量测试**：`pytest tests/ -q` → **505 passed**（0 失败 0 错误）

### v1.3.4 (2024-08)

#### Bug 修复（第八轮生产环境修复，P0×3 + P1×1 + P2×1 + 环境×2）

> 本轮修复全部来自 Linux 服务器生产环境实测暴露的问题（构建失败、Web UI 白屏、防幻觉误报）。

- **P0 Embedding 批大小超 API 上限**：`text-embedding-v3` 单请求最多 10 条文本，默认 `embedding_batch_size=16` 导致构建知识库时全部批次 400 失败；默认值改为 10，并在 `BailianEmbedding` 构造时对超限配置钳制（>10 → 10，非整数回退）并告警，存量 `.env` 无需修改
- **P0 确定性 API 错误被无效重试且无详情**：Embedding / LLM / Reranker 非 200 分支补全服务端 `resp.message`（此前只记状态码，根因不可见）；4xx（除 429 限流）为确定性客户端错误，新增 `FatalAPIError` 快速失败不再重试，429/5xx 仍按原退避重试
- **P0 Web UI 白屏（Gradio 6 兼容）**：Gradio 6.0 移除 `Chatbot(show_copy_button/bubble_full_width)` 与 `Blocks(theme/css)` 参数，按主版本分支兼容（6.x 用 `buttons=["copy"]`/`layout="bubble"`，theme/css 移到 `launch()`），4/5/6.x 均可运行
- **P0 问答页面报错（Gradio 6 消息格式）**：Gradio 6.0 的 `Chatbot` 消息格式从 tuple 列表 `[(user, assistant)]` 改为 dict 列表 `[{"role", "content"}]`，`answer_question` 产出 tuple 历史导致 postprocess 校验失败、页面返回"错误"；新增 `_iter_history_pairs`（按元素类型自动检测格式）/ `_append_conversation` / `_update_last_assistant` 三个 helper，全部 history 操作按版本产出合法消息
- **P0 多轮对话崩溃（Gradio 6 多模态 content）**：Gradio 6 的 `Chatbot.preprocess` 将消息 content 从 str 强制转为 list[dict]（`[{"type": "text", "text": ...}]`），`_convert_history` 对 list 调 `.find()` 崩溃（多轮第二轮起必现）；新增 `_extract_text` 统一提取文本，`_iter_history_pairs` 对两种格式的 content 均归一化
- **P1 模型声明知识截止日期**：qwen 回答时效性问题习惯写"截止到2024年7月"（训练数据知识截止，非代码硬编码）；`_build_messages` 在 system prompt 统一注入当前日期并禁止"截止到XX年XX月/我的知识截止于XX"类表述，时效无法确认时提示以官方最新发布为准
- **P1 按需自动联网搜索**：新增 `LLM_ENABLE_SEARCH` 总开关（默认关），开启后开放类/未知类问题自动联网，30+ 时效关键词（最新/展览/门票/2026…）命中即联网，纯知识库事实问题不联网省费用；`enable_search` 并入缓存 key、system prompt 追加搜索引导（联网仅补时效，文物知识以 RAG 参考信息为准）
- **P0 刷新状态报错（qdrant-client 1.10+ 结构变更）**：`CollectionParams` 不再有顶层 `distance`（移入 `params.vectors`，单向量为 VectorParams/命名向量为 VectorParamsMap）；`get_stats` 防御性兼容新旧结构与命名向量
- **P0 对话区域消失（Gradio 6 emoji 头像）**：`avatar_images=(None, "🏛️")` 在 Gradio 6 被当作文件路径解析为无效 FileData，前端渲染 Chatbot 崩溃导致对话+检索区域一闪消失；移除 emoji 头像（`avatar_images=None`）；另发现服务器 gradio 前端资源旧版残留（index-BZvZc4Wo.js），重装 6.22.0 对齐（index-BgYNBSAi.js）
- **P1 推荐回答递归重复 → 根因纠正为 dashscope 流式合并模式**：未传 `incremental_output=True` 时 dashscope 对 qwen 系列默认返回"累积全文 chunk"（incremental_to_full），`full_answer += chunk` 按增量追加导致内容膨胀重复（实测 195 件文物）；`chat_stream` 显式传 `incremental_output=True` 后返回增量 token，拼接无重复（bug-102 的 prompt 防重复指令保留，不冲突）
- **P1 防幻觉检查误报**：`verify_answer_grounding` 将 LLM 回答中的结构化字段标签（`**推荐理由**` 等）当作名称、且名称变体（`清明上河图（北宋张择端本）` vs `清明上河图`）精确比较不匹配导致大面积误报；新增字段标签黑名单 + 名称变体（包含关系）匹配，真实幻觉仍能检出
- **P2 Web UI 白屏（依赖版本约束）**：Gradio 6.x 依赖 `starlette>=1.0.1`，但 starlette 1.4.0 的 `GZipResponder` 新增必填 keyword-only `thread_minimum_size` 与 gradio 6.22 不兼容（ASGI 请求崩溃）；requirements.txt 显式约束 `starlette>=1.0.1,<1.4` + `fastapi>=0.115.2,<1.0` 并注释说明
- **环境加固**：requirements.txt 补充配套版本约束与注释，防止新环境装到不兼容组合

#### 修改文件
- `src/embeddings.py`、`src/llm.py`、`src/reranker.py`、`src/utils.py`、`src/config.py`、`src/rag_pipeline.py`、`src/chunking.py`、`app.py`、`requirements.txt`、`tests/test_review_findings.py`、`tests/test_edge_cases.py`

**全量测试**：`pytest tests/ -q` → **223 passed**（第七轮 186 + 本轮新增 37）

---

### v1.3.3 (2024-08)

#### Bug 修复（第七轮复测，P0×2 + P1×6 + 连带 P0×1）
- **P0 重建时向量库残留陈旧数据**：`build_knowledge_base` / `build_knowledge_base_from_documents` 在 `overwrite=False` 重建后调用新增的 `VectorStore.delete_stale_chunks()`，清理已移除/变更切片的旧向量，避免语义检索返回知识库中已不存在的陈旧结果（与 BM25/缓存不一致）
- **P0 重复添加产生重复切片**：`add_artifacts` 合并新旧切片时按 `chunk.id` 去重，避免同一文物重复添加导致 BM25 索引与缓存文件出现重复切片（向量库按 ID 幂等覆盖不重复），修复三大存储状态不一致
- **P0 连带（语义检索不可用）**：qdrant-client ≥1.12 已移除弃用的 `search` 方法，`vector_store.search` 直接调用会 AttributeError 导致语义检索永远失败；改为按客户端能力选择 `query_points`（新版）/ `search`（旧版）并保持返回格式一致
- **多轮对话上下文截断**：`_convert_history` 改为按检索来源标记 `**📚 检索来源**` 定位截断，不再用旧分隔符 `\n---\n` 盲目分割，避免 LLM 回答正文中的 Markdown 水平线被误伤、后续内容丢失
- **Embedding 维度未校验**：`embed_one` / `_embed_batch` 校验返回向量维度与配置一致，维度不匹配时明确报错重试，不再以晦涩的 Qdrant 错误失败
- **LLM 响应缓存 key 不全**：`llm.chat` 缓存 key 补齐 `max_tokens` / `top_p` / 额外 kwargs，不同生成参数不再共享缓存条目
- **相关度分数阈值失真**：`format_answer` 分数阈值自适应——RRF 融合分（约 0.01 量级）按显示排名上色（第1名 🟢、第2-3名 🟡、其余 ⚪），重排分数（0~1）仍用固定阈值，不再所有结果恒为灰色
- **长文档内容静默丢失**：`load_all_as_artifacts` 对超长文档按 4500 字符切分为多个 Artifact（标题带"第 N/M 部分"），全文均可被切片检索，不再只索引前 5000 字符
- **项目切换关闭竞态**：`VectorStore.close()` 与懒连接共用 `_connect_lock`，避免项目切换关闭旧连接与在途请求建连交错执行

#### 修改文件
- `src/vector_store.py`、`src/rag_pipeline.py`、`src/embeddings.py`、`src/llm.py`、`src/document_loader.py`、`app.py`、`tests/test_edge_cases.py`、`tests/test_review_findings.py`

---

### v1.3.2 (2024-08)

#### Bug 修复（第六轮复测，P0×1 + P1×2）
- **P0 增量添加后检索缓存未失效**：`add_artifacts` 增量添加完成后清空检索缓存，避免旧检索结果在 TTL 内继续被命中（与知识库重建的 P0-1 修复保持一致）
- **项目切换后向量库客户端重连**：`VectorStore.reset_connection()` 在 pipeline 切换项目时关闭并重置 Qdrant 客户端，确保数据写入新项目的存储目录，不再写入旧项目目录
- **初始化并发预热竞态**：`init_pipeline` 将知识库预热移入锁内完成后才返回，并发请求在预热期间不再误报"知识库尚未构建"

#### 修改文件
- `src/rag_pipeline.py`、`src/vector_store.py`、`app.py`

---

### v1.3.1 (2024-08)

#### Bug 修复（第五轮复测，P0×1 + P1×7）
- **P0 检索缓存跨项目串数据**：缓存 key 加入 collection_name 隔离不同项目；知识库重建后自动清空检索缓存，避免多项目 Web UI 下相同问题命中他项目结果 / 重建后 TTL 内返回旧数据
- **API 非 200 响应退避重试**：LLM / Embedding / Reranker 三处 API 封装在 429 限流、5xx 等非 200 响应时同样退避后重试（此前仅异常分支退避）；流式模式非 200 响应正确进入重试逻辑
- **项目专属闲聊人设生效**：`_select_chitchat_prompt()` 优先使用项目自定义 chitchat 模板（博物馆/企业助手），此前 4 处硬编码全局模板导致项目人设从未生效
- **配置项全线接线**：`llm_temperature` / `llm_max_tokens` / `llm_top_p` / `embedding_batch_size` / `retriever_top_k` / `retriever_hybrid_weight` / `reranker_enabled` 全部接入对应模块与查询默认参数（修改 .env 即生效）
- **增量添加健壮性**：`add_artifacts` 在 Qdrant 集合缺失时自动创建，不再崩溃
- **缓存防御加固**：Embedding 模式缓存加载校验值类型（list[float]），损坏文件不再导致查询崩溃
- **并发安全**：`VectorStore.close()` 后禁止惰性重连，避免切换项目时新旧实例在同一 Qdrant 本地路径双客户端冲突
- **构建一致性**：`build_knowledge_base` / `build_knowledge_base_from_documents` 不再静默忽略传入的 `project_id`，并同步更新向量库指向

#### 修改文件
- `src/retriever.py`、`src/rag_pipeline.py`、`src/cache.py`、`src/llm.py`、`src/embeddings.py`、`src/reranker.py`、`src/vector_store.py`、`tests/test_pipeline.py`

---

### v1.3.0 (2024-01)

#### 新增功能
- **多项目架构**：新增 `ProjectManager` 项目管理模块，支持多项目独立配置、独立 Qdrant 集合、独立 BM25 索引、独立 Prompt 模板
- **内置项目**：博物馆（`museum`）+ 企业（`enterprise`）两个内置项目，一键切换
- **下拉菜单切换**：Web UI 新增项目选择器，切换项目自动重建 Pipeline
- **独立部署**：每个项目可独立启动服务实例（不同端口），完全进程隔离

#### 代码泛化性提升
- **Prompt 模板**：从"中国文物专家"改为"知识助手"，不绑定任何领域
- **`is_kb_related()`**：移除领域特定关键词，纯通用闲聊模式匹配
- **查询分类**：移除"镇馆之宝""国宝""材质""工艺"等领域特定词
- **上下文格式**：从 `【文物：xxx】` 改为通用 `【xxx】`
- **数据模型**：`Artifact` 类支持任意领域字段，通过 `extra` 字典扩展

#### 新增文件
- `src/project.py` — 项目管理模块
- `data/projects/museum.json` — 博物馆项目配置
- `data/projects/enterprise.json` — 企业项目配置
- `scripts/generate_mock_project_data.py` — 多项目 Mock 数据生成器

#### 修改文件
- `src/rag_pipeline.py` — 集成 `project_id`，按项目选择 Prompt/集合/BM25/路径
- `src/vector_store.py` — 支持 `project_id`，自动使用项目专属集合名和存储路径
- `scripts/build_knowledge_base.py` — 新增 `--project` 和 `--list-projects` 参数
- `app.py` — 新增项目下拉选择器

#### Bug 修复
- `is_kb_related()` 修复：移除 `len(q) <= 2` 过度过滤，允许单/双字合法查询（如"鼎""剑"）
- `verify_answer_grounding()` 测试修复：上下文格式统一为 `【xxx】` 而非 `【文物：xxx】`
- `query_stream()` 修复：补充缺失的 `classify` 时间记录
- 缓存文件扩展名修正：`exact_cache.pkl` → `exact_cache.json`
- `vector_store.py` 内存模式注释修正：明确为本地持久化模式而非纯 RAM 模式
- `app.py` 状态检查修复：`get_system_status()` 现在感知当前项目

---

### v1.2.1 (2024-01)

#### 模型迁移
- **重排序模型迁移**：从已下线的 `gte-rerank` 迁移至 `qwen3-reranker-4b`（默认），支持 `qwen3-reranker-8b`（更准）
- 配置项 `RERANKER_MODEL` 默认值已更新，`.env.example` 同步更新

---

### v1.2.0 (2024-01)

#### 新增功能
- **闲聊路由**：自动识别闲聊问题（你好、天气、你是谁），直接 LLM 回答，不走 RAG
- **检索结果可视化**：Web UI 右侧面板实时显示检索到的条目、相关度得分、切片类型
- **响应时间显示**：每条回答底部显示总耗时（分类、检索、重排序、LLM 各阶段）
- **智能切片 v2**：从 4 切片优化为 3 切片（summary/detail/significance），信息密度更高
- **查询分类 v2**：基于评分机制，准确识别 15+ 种查询模式
- **多轮对话**：支持追问，保留最近 4 轮对话历史
- **增量更新**：`add_artifacts()` 方法支持增量添加数据，无需全量重建
- **回答质量评估**：`verify_answer_grounding()` 检查 LLM 回答是否基于检索上下文

#### 性能优化
- **闲聊路由**：非知识库问题跳过检索，响应时间 < 500ms
- **重排序优化**：检索结果 <= 3 条时跳过重排序，节省 200ms
- **响应时间分解**：每阶段耗时可追踪
- **高频问题 Embedding 预计算**：16 个高频问题模式离线预计算，命中率 ~30-50%
- **Qdrant 本地持久模式**：可选，查询快，重启后数据保留

#### 响应时间分析

| 场景 | 总耗时 | 检索 | LLM 首字 | 说明 |
|------|--------|------|---------|------|
| 闲聊问候 | **~300-500ms** | 0ms | 300-500ms | 直接 LLM，不走 RAG |
| 知识库（缓存命中） | **~500-800ms** | < 5ms | 500-800ms | Embedding 预计算命中 |
| 知识库（流式） | **~1-2s 首字** | 300-500ms | 500-800ms | 并行检索 + 流式 LLM |
| 知识库（非流式） | **~2-4s 完整** | 300-500ms | 1500-3000ms | 等待 LLM 完整输出 |

---

### v1.1.0 (2024-01)

#### 新增功能
- **Web UI 问答界面**：基于 Gradio 的浏览器端问答页面，支持流式输出
- **多格式文档支持**：PDF、Word、TXT、Markdown、图片(OCR) 等多种格式自动解析
- **LRU 缓存系统**：Embedding、LLM 响应、检索结果三层缓存，相同问题秒回
- **Mock 数据生成器**：生成测试数据
- **Conda 环境配置**：`environment.yml` 一键创建隔离环境
- **GPU 一键部署脚本**：`setup_gpu.sh` 自动完成部署全流程

#### 性能优化
- **并行检索**：语义检索和 BM25 检索同时执行，检索速度提升 ~40%
- **流式输出**：首 token 延迟优化，检索完成后立即返回
- **上下文裁剪**：自动裁剪上下文至 10000 字符内，防止 Token 超限
- **预热机制**：启动时预先加载知识库，减少首次查询延迟

#### 模型切换
- 默认 LLM 从 `qwen-max` 切换为 `qwen-plus`（速度快 2 倍，成本低 10 倍）

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户接口层                                    │
│  ┌─────────────┐  ┌────────────────┐  ┌─────────────────────────┐  │
│  │ Gradio Web  │  │ 交互式 CLI     │  │ Python API (SDK)       │  │
│  │ (app.py)    │  │ (run_qa.py)    │  │ (RAGPipeline 类)       │  │
│  └──────┬──────┘  └───────┬────────┘  └───────────┬─────────────┘  │
└─────────┼──────────────────┼──────────────────────┼────────────────┘
          │                  │                      │
┌─────────▼──────────────────▼──────────────────────▼────────────────┐
│                      RAG 流水线（rag_pipeline.py）                  │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │ 查询分类  │→ │ 混合检索  │→ │ 重排序   │→ │ Prompt构建 + LLM  │  │
│  │ classify │  │ hybrid   │  │ reranker │  │ generation         │  │
│  └──────────┘  └────┬─────┘  └──────────┘  └────────────────────┘  │
└─────────────────────┼──────────────────────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────────────────────┐
│                      ProjectManager                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ 博物馆项目    │  │ 企业项目      │  │ 自定义项目    │  ...        │
│  │ museum       │  │ enterprise   │  │ custom       │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
│         │                 │                 │                       │
│         ▼                 ▼                 ▼                       │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐                  │
│  │ Qdrant   │      │ Qdrant   │      │ Qdrant   │  ← 独立集合      │
│  │ museum   │      │ enterpr. │      │ custom   │                  │
│  ├──────────┤      ├──────────┤      ├──────────┤                  │
│  │ BM25 idx │      │ BM25 idx │      │ BM25 idx │  ← 独立索引      │
│  ├──────────┤      ├──────────┤      ├──────────┤                  │
│  │ Prompt   │      │ Prompt   │      │ Prompt   │  ← 独立Prompt    │
│  └──────────┘      └──────────┘      └──────────┘                  │
└────────────────────────────────────────────────────────────────────┘
```

### 数据流

```
用户输入 → 路由判断(is_kb_related)  [L0 规则层，零成本]
  ├── 闲聊/非知识库 → 直接 LLM (qwen-plus) ← 无检索，最快
  └── 知识库相关 → 分层意图分类 [bug-113]
                    L1 语义(SemanticIntentClassifier) 置信度≥0.50 → 直接采用
                    L2 LLM(classify_with_llm) 低置信度时兜底
                    L0 规则评分(classify_query) 保住底
                    L1/L2 判为闲聊 → 转闲聊分支
                    ▼
        推荐/事实/比较/开放 → 混合检索(并行)
                    ─┬─ 语义: Embedding → Qdrant
                     └─ BM25: rank-bm25 (内存索引)
                    │  RRF融合 + 去重
                    ▼
        重排序 (Qwen3-Reranker / TF-IDF fallback)
                    │
                    ▼
        构建上下文(裁剪 ≤ 10000字符) + 选择Prompt(按项目)
                    │
                    ▼
        LLM生成 (qwen-plus 流式/非流式)
                    │
                    ▼
        输出 (Web UI / CLI) + 检索结果可视化
```

---

## 技术栈

| 环节 | 技术选型 | 版本 | 功能 | 部署方式 |
|------|---------|------|------|---------|
| **数据加载** | 自定义 `DataLoader` | v1 | 加载 JSON/CSV 数据，字段映射标准化 | 内置，零依赖 |
| **多格式文档解析** | pypdf / python-docx / python-pptx / PaddleOCR | 最新 | 解析 PDF、Word、PPT、图片(OCR) 等格式 | 本地（GPU 可选） |
| **文档切片** | 自定义 `SmartChunking` | v2 | 每项生成 3 个切片（summary/detail/significance），信息密度高、重叠少 | 内置 |
| **Embedding 生成** | 阿里云百炼 `text-embedding-v4` | v4 | 1024 维向量，中文语义理解优秀，批处理并发（bug-110 已从 v3 升级） | **在线 API** |
| **Embedding 缓存** | 自定义 `EmbeddingCache` | v2 | 高频问题预计算 + 精确匹配 + 模式匹配，持久化到磁盘 | 内置 |
| **向量数据库** | **Qdrant**（本地模式） | ≥1.9 | 本地持久化，零配置；支持本地持久模式（查询快） | 本地 |
| **关键词检索** | **rank-bm25**（BM25Okapi） | ≥0.2 | 中文 unigram 分词，与语义检索互补 | 内置（内存索引） |
| **混合检索融合** | 自定义 `HybridRetriever` | v2 | 语义检索 + BM25 并行执行，RRF 算法融合排序，去重 | 内置 |
| **重排序** | 百炼 **`qwen3-reranker-4b`**（默认）/ `qwen3-reranker-8b` / 本地 TF-IDF（降级） | — | 对检索结果精排 | 在线 API / 本地 |
| **LLM 问答** | 阿里云百炼 **`qwen-plus`**（默认）/ `qwen-max` | 3.7+ | 日常问答用 qwen-plus，复杂推理用 qwen-max | **在线 API** |
| **LLM 缓存** | 自定义 `LRUCache` | v1 | 相同问题不重复调用 API，TTL 30 分钟 | 内置 |
| **查询分类** | 自定义 `classify_query` + `SemanticIntentClassifier` | v3 | 分层级联：L0 规则闲聊路由 → L1 向量语义分类（原型相似度）→ L2 LLM 兜底（低置信时）→ 规则评分保底（bug-113） | 内置 + 在线 API（L2） |
| **闲聊路由** | 自定义 `is_kb_related` | v1 | 自动识别问候、天气等非知识库问题，直接 LLM 回答 | 内置 |
| **上下文裁剪** | 自定义 `_trim_context` | v1 | 按相关性保留完整段落，上限 10000 字符 | 内置 |
| **多轮对话** | 对话历史传递 | v1 | 保留最近 4 轮对话（8 条消息），支持追问 | 内置 |
| **回答质量评估** | 自定义 `verify_answer_grounding` | v1 | 检查回答中的名称是否在检索上下文中，防幻觉 | 内置 |
| **增量更新** | 自定义 `add_artifacts` | v1 | 增量添加新数据，无需全量重建 | 内置 |
| **项目管理** | 自定义 `ProjectManager` | v1 | 多项目配置、Prompt 隔离、独立集合/索引 | 内置 |
| **Web UI** | **Gradio** | ≥4.44 | 浏览器端问答界面，流式输出，项目选择器，检索结果可视化 | 本地 |
| **CLI 交互** | **Rich** | ≥13.7 | 终端交互式问答，支持彩色输出、表格、Markdown 渲染 | 内置 |
| **缓存层** | 自定义三层缓存 | v2 | Embedding 缓存（持久化）+ LLM 响应缓存（LRU）+ 检索结果缓存（LRU） | 内置 |
| **配置管理** | **Pydantic Settings** | ≥2.1 | 支持 .env 文件 + 环境变量 + 类型校验 | 内置 |
| **日志** | **Loguru** | ≥0.7 | 彩色控制台输出 + 文件日志（自动轮转、保留 30 天） | 内置 |
| **包管理** | **Conda**（environment.yml） | — | 隔离环境，不影响其他项目 | 系统 |
| **GPU 部署** | 自定义 `setup_gpu.sh` | v1 | 自动检测 NVIDIA 驱动、创建 Conda 环境、安装依赖 | 系统 |

---

## 快速开始

### 1️⃣ 环境准备

```bash
# 进入项目目录
cd /path/to/project

# 创建 Conda 环境（推荐，服务器与开发机通用）
conda env create -f environment.yml
conda activate cultural-relics-rag

# 安装依赖
pip install -r requirements.txt
```

### 2️⃣ 配置 API Key

```bash
# 方式一：设置环境变量（推荐）
# Windows CMD
set DASHSCOPE_API_KEY=your-api-key-here
# Windows PowerShell
$env:DASHSCOPE_API_KEY="your-api-key-here"
# Linux/Mac
export DASHSCOPE_API_KEY="your-api-key-here"

# 方式二：创建 .env 文件（从模板复制）
cp .env.example .env
# 编辑 .env 文件，填入你的 API Key
```

### 3️⃣ 生成 Mock 数据

```bash
# 一键生成两个项目的测试数据
python scripts/generate_mock_project_data.py
```

### 4️⃣ 构建知识库

```bash
# 构建博物馆项目知识库
python scripts/build_knowledge_base.py --project museum --source json

# 构建企业项目知识库
python scripts/build_knowledge_base.py --project enterprise --source json

# 查看所有可用项目
python scripts/build_knowledge_base.py --list-projects
```

成功输出示例：
```
============================================================
知识库构建完成！
  - artifacts: 17
  - chunks: 42
  - vectors: 42
============================================================
```

### 5️⃣ 运行问答系统

```bash
# 启动博物馆问答服务（Web UI）
python app.py --project museum

# 另开终端，启动企业问答服务（不同端口）
python app.py --project enterprise --port 7861

# 终端交互模式（指定项目）
python scripts/run_qa.py --project museum

# 单次查询
python scripts/run_qa.py -q "推荐一些代表性的文物" --project museum
```

---

## 使用指南

### 交互式命令

在交互式模式下，可使用以下命令：

| 命令 | 功能 |
|------|------|
| `/stats` | 查看知识库统计信息 |
| `/context` | 切换是否显示检索上下文 |
| `/rerank` | 切换是否启用重排序 |
| `/clear` | 清屏 |
| `/exit` 或 `/quit` | 退出系统 |

### 示例查询

```bash
# 推荐类问题（博物馆项目）
python scripts/run_qa.py -q "推荐一些代表性的文物" --project museum
python scripts/run_qa.py -q "有哪些必看的展览" --project museum

# 推荐类问题（企业项目）
python scripts/run_qa.py -q "公司有哪些主要产品" --project enterprise
python scripts/run_qa.py -q "推荐几个成功案例" --project enterprise

# 事实类问题（博物馆项目）
python scripts/run_qa.py -q "司母戊鼎有多重" --project museum
python scripts/run_qa.py -q "清明上河图在哪里展出" --project museum

# 事实类问题（企业项目）
python scripts/run_qa.py -q "员工入职流程是什么" --project enterprise
python scripts/run_qa.py -q "公司差旅报销标准是什么" --project enterprise

# 比较类问题
python scripts/run_qa.py -q "青铜器和瓷器有什么区别" --project museum

# 闲聊问题（自动识别，不走 RAG）
python scripts/run_qa.py -q "你好，你是谁"
python scripts/run_qa.py -q "今天天气怎么样"
```

---

## 语音功能使用指南（bug-121）

### 1. 配置

在 `.env` 中配置（模板见 `.env.example`）：

```bash
# 讯飞语音听写（ASR）
XFYUN_APP_ID=your-app-id
XFYUN_API_KEY=your-api-key
XFYUN_API_SECRET=your-api-secret

# 阿里云百炼 TTS（复用 DASHSCOPE_API_KEY）
DASHSCOPE_API_KEY=sk-xxx
TTS_ENABLED=true
TTS_MODEL=cosyvoice-v3-flash
TTS_VOICE=longanyang          # 默认小男孩音色
TTS_CHUNK_CHARS=1000
```

### 2. 语音输入（ASR）

1. 点击「语音输入」面板的麦克风图标，开始说话
2. 识别文字**边说边出字**，实时填入输入框
3. 说完**停顿约 2 秒**自动结束转写（服务端 VAD 静音检测）；最长 30 秒兜底
4. 识别结果可**直接改写**，再点「发送」

> 说明：静默自动结束只作用于转写；浏览器录音需**再点一次麦克风**手动停止（Gradio 原生限制，无需自定义 JS）。

### 3. 多音字 / 热词配置

配置文件：`data/voice/asr_dict.json`（全局）与 `data/voice/{project_id}_asr_dict.json`（项目覆盖，优先级更高）：

```json
{
  "hotwords": ["司母戊鼎", "清明上河图"],
  "corrections": {"四亩无顶": "司母戊鼎"}
}
```

- **hotwords**：热词列表。⚠️ 一期暂不生效——讯飞 IAT v2 接口不支持 `business.hotwords` 参数
  （实测拒绝 10163），需走「热词表 vocabulary_id」接口，二期接入；配置格式与拼音标注
  `词(拼音)` 已预留
- **corrections**：纠错映射（识别结果后处理替换，**立即生效**）。键为可能识别错的写法，值为正确写法，
  多字符键优先匹配

### 4. 语音播报（TTS）

- 回答完成后**自动播报**（句子级流式：第一句合成完即播，逐句无缝续播）；「语音播报」开关默认开启，可关闭
- 播报完成后「重播」区出现完整音频，可点击重听
- 更换音色：修改 `.env` 的 `TTS_VOICE`（`cosyvoice-v3-flash` 可用音色以百炼控制台为准）

### 5. 二期：定制真人音色（cosyvoice-v3.5-flash）

1. 在百炼控制台开通 `cosyvoice-v3.5-flash` 模型
2. 用音色定制接口注册音色（需提供一段真人录音样本，如 10-60 秒清晰人声 wav）：

```python
from dashscope.audio.tts_v2 import VoiceEnrollmentService
service = VoiceEnrollmentService()
result = service.create_voice(
    target_model="cosyvoice-v3.5-flash",
    prefix="my_voice",
    url="https://your-host/sample.wav",  # 录音样本公网可访问地址
    language_hints=["zh"],
)
# 返回 voice_id，如 my_voice_xxx
```

3. `.env` 切换：`TTS_MODEL=cosyvoice-v3.5-flash`、`TTS_VOICE=<voice_id>`，重启即可
4. 音色管理：`list_voices` / `query_voice` / `update_voice` / `delete_voice`（见 dashscope SDK 文档）

---

## 数据处理流程

### 1. 原始数据

每个项目的数据存储在 `data/raw/{project_id}/data.json`，每条记录包含以下字段：

```json
{
  "name": "司母戊鼎（后母戊鼎）",
  "dynasty": "商代晚期",
  "category": "青铜器",
  "material": "青铜",
  "provenance": "1939年河南省安阳市武官村",
  "location": "中国国家博物馆",
  "description": "是目前已知中国古代最重的青铜器...",
  "historical_significance": "代表了商代青铜铸造技术的巅峰...",
  "cultural_value": "作为中国国家博物馆的镇馆之宝...",
  "tags": ["国宝", "青铜器", "商代", "礼器"],
  "importance": 5
}
```

> **注意**：所有字段均为可选，系统会自动识别。对于企业项目等非文物场景，`dynasty`、`material`、`provenance` 等字段可以为空。

### 2. 智能切片（v2）

每项数据生成 **3 种类型**的切片：

| 切片类型 | 内容 | 目的 |
|---------|------|------|
| **summary** | 名称 + 朝代 + 类别 + 标签 + 一句话亮点 | 快速匹配和推荐类问题 |
| **detail** | 完整描述信息（材质、出土地、现藏地） | 事实类问题 |
| **significance** | 历史意义 + 文化价值 | 推荐类问题（回答"为什么有代表性"） |

### 3. Embedding 生成

使用百炼 `text-embedding-v4` 模型（bug-110 已从 v3 升级），每批最多 10 条并发，自动重试，缓存持久化。

### 4. 数据入库

- **向量数据库**：Qdrant 本地持久化（`data/processed/{project_id}/qdrant_db/`）
- **BM25 索引**：内存中构建，用于关键词检索
- **切片缓存**：JSON 格式保存（`data/processed/{project_id}/chunks.json`）

---

## RAG 问答流程

### 完整处理流程

以用户提问 **"推荐一些代表性的文物"** 为例：

```
Step 1: 查询分类（分层级联，bug-113）
────────────────────────────────────────────────────────────
输入: "推荐一些代表性的文物"
输出: QueryType.RECOMMENDATION (method=semantic)
逻辑: L1 语义分类——问题 embedding 与 5 类意图原型（推荐/事实/比较/开放/闲聊）
      计算余弦相似度，最高分 0.615 ≥ 阈值 0.50 → 直接采用（零额外 LLM 成本）
      若置信度 < 0.50 → L2 LLM 兜底分类 → 仍失败/无 Key → 规则评分保住底

Step 2: 混合检索（并行）
────────────────────────────────────────────────────────────
a) 语义检索（向量）:
   用户问题 → text-embedding-v4 → 1024维向量
   → Qdrant 余弦相似度搜索 → Top 20

b) BM25 关键词检索:
   用户问题 → 分词 → BM25 打分 → Top 20

c) RRF 融合:
   对两个结果集做 Reciprocal Rank Fusion
   语义权重 0.7 : BM25 权重 0.3

d) 去重：
   同一 ID 只保留最高分结果

输出: Top 10（覆盖不同类别）

Step 3: 重排序（可选）
────────────────────────────────────────────────────────────
使用百炼 Qwen3-Reranker API 对 Top 10 进行精细排序
或本地 TF-IDF 余弦相似度自动降级

输出: Top 5（精排后）

Step 4: 构建 Prompt
────────────────────────────────────────────────────────────
系统提示词模板（推荐类，按项目选择）:
  你是一位专业的知识助手...
  推荐原则：
  1. 从参考信息中挑选 3~5 个最具代表性的结果
  2. 每个推荐项需包含：名称、介绍、推荐理由
  3. 尽量覆盖不同类型
  4. 推荐理由要具体
  5. 如果参考信息不足，如实说明

  参考信息：
  【司母戊鼎】...
  【清明上河图】...
  ...

  用户问题：推荐一些代表性的文物

Step 5: LLM 生成
────────────────────────────────────────────────────────────
调用 qwen-plus → 生成结构化推荐回答

Step 6: 输出
────────────────────────────────────────────────────────────
### 推荐清单

**1. 司母戊鼎（商代晚期）**
- **简介**：目前已知中国最重的青铜器，重832.84公斤
- **推荐理由**：代表商代青铜铸造巅峰，中华文明象征
- **收藏地点**：中国国家博物馆
...
```

---

## 多项目架构

### 架构设计

```
┌──────────────────────────────────────────────────────────────────┐
│                      ProjectManager                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ 博物馆项目    │  │ 企业项目     │  │ 自定义项目   │  ...        │
│  │ museum      │  │ enterprise  │  │ custom      │              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
│         │                │                │                      │
│         ▼                ▼                ▼                      │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ 共享基础设施层                                            │    │
│  │  Embedding(百炼) + LLM(qwen-plus) + Reranker(Qwen3)      │    │
│  └──────────────────────────────────────────────────────────┘    │
│         │                │                │                      │
│         ▼                ▼                ▼                      │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐                   │
│  │ Qdrant   │    │ Qdrant   │    │ Qdrant   │  ← 独立集合      │
│  │ museum   │    │ enterpr. │    │ custom   │                   │
│  ├──────────┤    ├──────────┤    ├──────────┤                   │
│  │ BM25 idx │    │ BM25 idx │    │ BM25 idx │  ← 独立索引      │
│  ├──────────┤    ├──────────┤    ├──────────┤                   │
│  │ Prompt   │    │ Prompt   │    │ Prompt   │  ← 独立Prompt    │
│  └──────────┘    └──────────┘    └──────────┘                   │
└──────────────────────────────────────────────────────────────────┘
```

### 核心特性

| 特性 | 说明 |
|------|------|
| **项目隔离** | 每个项目独占 Qdrant 集合 + BM25 索引 + Prompt 模板 + 数据目录 |
| **独立部署** | 每个项目可独立启动服务实例（不同端口），完全进程隔离 |
| **快速添加** | 新项目只需创建 JSON 配置 + 准备数据，零代码修改 |
| **共享基础设施** | Embedding、LLM、Reranker 等 API 调用共用，不重复配置 |

### 启动多个项目服务

```bash
# 终端1：博物馆项目（端口 7860）
python app.py --project museum --port 7860

# 终端2：企业项目（端口 7861）
python app.py --project enterprise --port 7861

# 终端3：自定义项目（端口 7862）
python app.py --project custom --port 7862
```

每个服务实例独立进程、独立端口、独立集合，互不干扰。

### 添加新项目

只需 3 步，无需修改任何代码：

```bash
# 1. 创建项目配置
cat > data/projects/custom.json << 'EOF'
{
  "id": "custom",
  "name": "自定义项目",
  "description": "项目描述",
  "collection_name": "project_custom",
  "prompts": {
    "recommend": "你是一位专业顾问...{context}",
    "factual": "...{context}",
    "default": "...{context}",
    "chitchat": "..."
  }
}
EOF

# 2. 准备数据
mkdir -p data/raw/custom
# 将数据文件放入 data/raw/custom/data.json

# 3. 构建知识库
python scripts/build_knowledge_base.py --project custom --source json
```

> 如果不提供 `prompts` 字段，系统会自动使用通用模板。

### 内置项目

| 项目 | ID | 数据量 | 数据内容 | Prompt 风格 |
|------|-----|-------|---------|------------|
| 博物馆 | `museum` | 17 条 | 文物、展览、参观须知 | 博物馆专家 |
| 企业 | `enterprise` | 14 条 | 企业概况、产品方案、案例、文档 | 企业顾问 |

### 数据隔离说明

```
data/processed/
├── museum/              # 博物馆项目
│   ├── chunks.json      # 切片缓存（独立）
│   └── qdrant_db/       # Qdrant 数据库（独立集合 project_museum）
└── enterprise/          # 企业项目
    ├── chunks.json      # 切片缓存（独立）
    └── qdrant_db/       # Qdrant 数据库（独立集合 project_enterprise）
```

---

## 多格式文档支持

系统支持从多种格式的文档中提取知识，统一入库检索。

### 支持的文档格式

| 格式 | 扩展名 | 解析引擎 | 说明 |
|------|--------|---------|------|
| **纯文本** | `.txt`, `.md`, `.csv` | 内置 | 直接读取 UTF-8 文本 |
| **JSON** | `.json` | 内置 | 自动解析结构化数据 |
| **PDF** | `.pdf` | pypdf | 提取文本内容和元数据 |
| **Word** | `.docx` | python-docx | 提取段落和表格 |
| **PPT** | `.pptx`, `.ppt` | python-pptx | 提取幻灯片文字 |
| **Excel** | `.xlsx` | openpyxl | 表格型数据：每 sheet 第一行为表头，每行一条记录；任意列可检索（bug-109） |
| **图片** | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`, `.tiff` | PaddleOCR (GPU) / Tesseract | OCR 文字识别 |

### 使用示例

```bash
# 从文档目录构建知识库（指定项目）
python scripts/build_knowledge_base.py --project museum --source docs --doc-path ./data/raw/museum/docs

# 混合模式（JSON 数据 + 文档）
python scripts/build_knowledge_base.py --project enterprise --source mixed

# Excel 表格数据（.xlsx）：直接放进文档目录（docs 模式自动识别）
python scripts/build_knowledge_base.py --project jiabohui --source docs --doc-path ./data/raw/jiabohui
# 或显式指定 Excel 文件（json 模式，注意必须 --json-path）
python scripts/build_knowledge_base.py --project jiabohui --source json --json-path ./data/raw/jiabohui/参展商名单.xlsx

# 禁用 OCR
python scripts/build_knowledge_base.py --project museum --source docs --no-ocr
```

---

## API 参考

### RAGPipeline 类

```python
from src.rag_pipeline import RAGPipeline

# 初始化（指定项目）
pipeline = RAGPipeline(
    project_id="museum",         # 项目 ID
    local_mode=True,             # Qdrant 本地模式
    enable_cache=True,           # 启用缓存
    memory_mode=False,           # Qdrant 本地持久模式
)

# 构建知识库
stats = pipeline.build_knowledge_base(
    data_path="data/raw/museum/data.json",
    overwrite=True,
)

# 执行查询（非流式）
result = pipeline.query(
    question="推荐一些代表性的文物",
    top_k=10,
    rerank=True,
    conversation_history=conversation_history,  # 多轮对话
)

# 结果字段
# result["answer"]              - LLM 生成的回答
# result["query_type"]          - 查询类型（recommendation/factual/...）
# result["retrieved_chunks"]    - 检索到的上下文
# result["context"]             - 拼接后的上下文文本
# result["timing"]              - 各阶段耗时
# result["from_kb"]             - 是否来自知识库

# 流式查询
for item in pipeline.query_stream(
    question="推荐一些代表性的文物",
    top_k=10,
    rerank=True,
    conversation_history=conversation_history,
):
    if isinstance(item, dict) and item.get("type") == "meta":
        # 检索结果元数据
        chunks_info = item["chunks"]
        timing = item["timing"]
    else:
        # 逐 token 文本
        print(item, end="")
```

### ProjectManager

```python
from src.project import project_manager

# 切换项目
cfg = project_manager.switch_to("museum")
print(cfg.name)              # "博物馆知识库"
print(cfg.collection_name)   # "project_museum"

# 获取项目配置（不切换）
cfg = project_manager.get_project("enterprise")

# 列出所有项目
projects = project_manager.list_projects()
# [{"id": "museum", "name": "博物馆知识库", ...}, ...]

# 动态添加项目
project_manager.add_project({
    "id": "custom",
    "name": "自定义项目",
    "collection_name": "project_custom",
    "prompts": {...},
})
```

### 其他核心模块

```python
# 数据加载
from src.data_loader import DataLoader
artifacts = DataLoader.load("data/raw/museum/data.json")

# 切片
from src.chunking import ChunkingPipeline, SmartChunking
pipeline = ChunkingPipeline(strategy=SmartChunking())
chunks = pipeline.process(artifacts)

# 混合检索
from src.retriever import HybridRetriever
results = retriever.retrieve(query="青铜器", top_k=10)
```

---

## Conda 环境部署（GPU 服务器）

### 一键部署脚本

```bash
# 进入项目目录
cd /path/to/project

# 运行部署脚本（自动完成所有步骤）
bash setup_gpu.sh
```

### 手动部署步骤

#### 1. 创建 Conda 环境

```bash
# 从 environment.yml 创建环境（仅包含 conda 包，速度更快）
conda env create -f environment.yml

# 激活环境
conda activate cultural-relics-rag

# 安装 pip 包（pip 包与 conda 包分离，避免 conda 卡住）
pip install -r requirements.txt
```

#### 2. 安装 PaddleOCR GPU 支持（可选）

```bash
# 安装 PaddlePaddle GPU 版
pip install paddlepaddle-gpu>=2.6.0

# 安装 PaddleOCR
pip install paddleocr>=2.7.0

# 验证 GPU 可用
python -c "import paddle; print('GPU可用:', paddle.is_compiled_with_cuda())"
```

#### 3. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入 DASHSCOPE_API_KEY
nano .env
```

#### 4. 生成测试数据并构建知识库

```bash
# 生成测试数据
python scripts/generate_mock_project_data.py

# 构建博物馆项目知识库
python scripts/build_knowledge_base.py --project museum --source json

# 构建企业项目知识库
python scripts/build_knowledge_base.py --project enterprise --source json
```

#### 5. 运行问答服务

```bash
# 启动博物馆问答服务（Web UI）
python app.py --project museum --host 0.0.0.0 --port 7860

# 启动企业问答服务（可选，不同端口）
python app.py --project enterprise --host 0.0.0.0 --port 7861
```

---

## 部署指南

### 开发环境（Windows）

```bash
# 本地运行
python scripts/generate_mock_project_data.py
python scripts/build_knowledge_base.py --project museum --source json
python scripts/run_qa.py --project museum
```

### 生产环境（GPU 服务器 Linux）

本项目设计为**纯 API 调用**，GPU 服务器主要用于 PaddleOCR 加速。

```bash
# 1. 创建 Conda 环境并安装依赖
conda env create -f environment.yml
conda activate cultural-relics-rag
pip install -r requirements.txt

# 2. 配置环境变量
export DASHSCOPE_API_KEY="your-api-key"

# 4. 生成数据并构建知识库
python scripts/generate_mock_project_data.py
python scripts/build_knowledge_base.py --project museum --source json

# 5. 启动 Web 服务
python app.py --project museum --host 0.0.0.0 --port 7860
```

### 独立部署多个项目

```bash
# 终端1：博物馆项目（端口 7860）
python app.py --project museum --port 7860

# 终端2：企业项目（端口 7861）
python app.py --project enterprise --port 7861

# 终端3：自定义项目（端口 7862）
python app.py --project custom --port 7862
```

---

## 性能优化

### 已实施的优化

| 优化项 | 收益 | 对准确率影响 | 原理 |
|--------|------|------------|------|
| **并行检索** | 检索速度提升 ~40% | 无影响 | 语义检索和 BM25 检索同时执行 |
| **HTTP Session 复用** | 首 token 延迟减少 ~200ms | 无影响 | 复用 TCP 连接，避免三次握手 |
| **LRU 响应缓存** | 重复问题秒回 | 无影响 | 相同问题缓存 30 分钟，有 TTL 过期 |
| **上下文裁剪** | 减少 Token 消耗 10-30% | 无影响 | 10000 字符上限，按相关性保留完整段落 |
| **重排序跳过** | 节省 ~200ms | 无影响 | 检索结果 ≤ 3 条时跳过重排序 |

### 响应时间优化说明

```
优化前：
  用户输入 → 查询分类 → 语义检索 → BM25检索 → RRF融合 → 重排序 → 构建Prompt → LLM生成
                                                                          ↓ 顺序执行
优化后：
  用户输入 → 查询分类 → ┌─ 语义检索 ─┐→ RRF融合 → 重排序 → 裁剪上下文 → LLM生成(流式)
                         ├─ BM25检索  ─┤                              ↓ 首字更快
                         └─ 并行执行  ─┘   HTTP连接复用 + 缓存       流式逐token输出
```

### 首 token 延迟优化要点

1. **并行检索**：语义检索（调用百炼 Embedding API + Qdrant 搜索）与 BM25 关键词检索同时进行
2. **HTTP 连接池**：复用长连接，避免每次 API 调用的 TCP 握手开销
3. **流式输出**：`query_stream()` 使用百炼 Stream 模式，首 token 在检索完成后立即返回
4. **缓存命中**：相同问题跳过检索和 LLM 调用，直接返回缓存结果
5. **预热机制**：`warmup()` 在启动时预先建立 HTTP 连接并加载 BM25 索引

---

## Web UI 问答界面

系统提供了基于 Gradio 的 Web 界面，支持流式输出、实时展示检索结果和项目切换。

### 启动方式

```bash
# 启动博物馆项目 Web UI
python app.py --project museum

# 启动企业项目 Web UI（不同端口）
python app.py --project enterprise --port 7861
```

### 参数说明

```bash
python app.py --help

# 指定端口和主机
python app.py --project museum --host 0.0.0.0 --port 7860

# 允许外部访问
python app.py --project museum --share

# 禁用流式输出
python app.py --project museum --no-stream
```

### 页面功能

- **项目选择器**：下拉菜单切换项目，自动重建 Pipeline
- **流式输出**：逐字显示答案，首字更快
- **检索结果可视化**：右侧面板实时显示检索到的条目、相关度得分、切片类型
- **闲聊路由**：自动识别问候、天气等非知识库问题，直接 AI 回答，跳过检索
- **响应时间**：每条回答底部显示总耗时（仅非流式）
- **状态监控**：实时显示当前项目的知识库统计和模型配置
- **对话历史**：支持多轮追问，保存最近 4 轮对话
- **示例问题**：一键点击尝试各种问题

---

## 项目结构

```
├── README.md                        # 本文档
├── project-context.md               # 开发上下文快照（用于 AI 助手续接开发）
├── requirements.txt                 # Python 依赖
├── environment.yml                  # Conda 环境配置（GPU 服务器）
├── setup_gpu.sh                     # GPU 服务器一键部署脚本
├── .env.example                     # 环境变量模板
├── .gitignore                       # Git 忽略规则
│
├── data/
│   ├── projects/                    # 项目配置文件（JSON）
│   │   ├── museum.json              #   博物馆项目
│   │   └── enterprise.json          #   企业项目
│   ├── raw/
│   │   ├── museum/                  # 博物馆项目原始数据
│   │   │   └── data.json            #   17 条数据
│   │   ├── enterprise/              # 企业项目原始数据
│   │   │   └── data.json            #   14 条数据
│   │   ├── artifacts.json           # 旧版单项目数据（保留，未使用）
│   │   └── docs/                    # 多格式测试文档目录
│   └── processed/                   # 处理后数据（按项目自动生成）
│       ├── museum/                  # 博物馆项目
│       │   ├── chunks.json
│       │   └── qdrant_db/
│       └── enterprise/              # 企业项目
│           ├── chunks.json
│           └── qdrant_db/
│
├── src/                             # 核心源代码
│   ├── __init__.py
│   ├── config.py                    # 配置管理（Pydantic Settings）
│   ├── utils.py                     # 工具函数
│   ├── cache.py                     # LRU 缓存（Embedding/LLM/检索结果）
│   ├── project.py                   # 项目管理（多项目配置、Prompt、隔离）
│   ├── data_loader.py               # 数据加载与标准化
│   ├── document_loader.py           # 多格式文档加载器（PDF/Word/图片OCR）
│   ├── chunking.py                  # 智能切片策略 v2
│   ├── embeddings.py                # 百炼 Embedding API 封装
│   ├── vector_store.py              # Qdrant 向量数据库封装
│   ├── retriever.py                 # 混合检索器（语义 + BM25，并行）
│   ├── reranker.py                  # 重排序模块（Qwen3-Reranker + TF-IDF fallback）
│   ├── llm.py                       # 百炼 Qwen API 封装
│   └── rag_pipeline.py              # RAG 流水线（核心编排）
│
├── app.py                           # Gradio Web UI 问答界面
│
├── scripts/                         # 可执行脚本
│   ├── __init__.py
│   ├── build_knowledge_base.py      # 构建知识库（支持 --project 参数）
│   ├── run_qa.py                    # 运行问答系统（交互式/单次查询）
│   ├── generate_mock_project_data.py # 多项目 Mock 数据生成器
│   ├── generate_mock_data.py        # 单项目 Mock 数据生成器（旧，保留）
│   └── generate_test_docs.py        # 多格式测试文档生成器
│
└── tests/                           # 单元测试
    ├── __init__.py
    ├── test_pipeline.py             # 流水线测试（75 个测试用例）
    ├── test_edge_cases.py           # 边界条件测试（65 个测试用例）
    └── test_review_findings.py      # 审查发现回归测试（45 个测试用例）
```

---

## 常见问题

### Q: API Key 在哪里获取？
A: 登录 [阿里云百炼平台](https://bailian.console.aliyun.com/) → 右上角"API 密钥" → 创建 API Key。

### Q: 费用如何？
A: 百炼 API 按量计费，qwen-plus 约 0.004元/千 tokens，text-embedding-v4 约 0.0007元/千 tokens。日常使用费用很低。

### Q: 如何添加更多数据？
A: 编辑 `data/raw/{project_id}/data.json`，按照已有格式添加新条目，然后重新运行：
```bash
python scripts/build_knowledge_base.py --project {project_id} --source json
```

### Q: 推荐结果不够多样化怎么办？
A: 可以在项目的 Prompt 模板中强调多样性要求，或调整 `top_k` 参数增加候选数量。

### Q: 如何切换成其他 LLM 模型？
A: 修改环境变量 `LLM_MODEL_NAME`，或在 `.env` 文件中设置。支持 `qwen-max`、`qwen-plus`、`qwen-turbo` 等。

### Q: 为什么回答会出现编造的内容？
A: 如果检索到的上下文不足，LLM 可能会"幻觉"。建议：
1. 增加知识库中的数据量
2. 降低 `llm_temperature` 参数
3. 在 Prompt 中强调"如果参考信息不足，请如实说明"

### Q: 如何创建一个新项目？
A: 只需 3 步：
1. 创建 `data/projects/{项目id}.json` 配置文件
2. 创建 `data/raw/{项目id}/data.json` 数据文件
3. 运行 `python scripts/build_knowledge_base.py --project {项目id} --source json`
无需修改任何代码。

### Q: Conda 环境的名称是什么？
A: `cultural-relics-rag`。使用 `conda activate cultural-relics-rag` 激活。

### Q: 构建知识库报 Embedding 400 错误怎么办？
A: 先看报错中的 `resp.message`（bug-095 起已输出服务端详情），常见三种原因：
1. **批量超限**：`text-embedding-v4` 单请求最多 10 条，报 `batch size ... larger than 10`。代码已默认 `EMBEDDING_BATCH_SIZE=10` 且自动钳制超限配置，无需手工处理；
2. **维度不匹配**：`EMBEDDING_DIMENSION` 需为模型支持值（1024/768/512/256/128/64）；
3. **文本超长**：单条文本超过模型 token 上限（8192 tokens），需缩短数据。

### Q: Web UI 白屏 / 页面无内容怎么排查？
A: 两步排查（bug-098/099/100）：
1. 看启动日志是否报 `Chatbot.__init__() got an unexpected keyword argument`——Gradio 6.0 移除了 `show_copy_button` 等参数，代码已按版本分支兼容（4/5/6.x 均可），请同步最新 `app.py`；
2. 看访问日志是否报 `GZipResponder.__init__() missing ... 'thread_minimum_size'`——**starlette 不能升到 1.4.x**（与 gradio 6.22 不兼容），请保持 `starlette>=1.0.1,<1.4`（已验证组合：gradio 6.22.0 + starlette 1.3.1 + fastapi 0.141.1）。

### Q: 重排模型报 `Model not exist` 怎么办？
A: 说明 `RERANKER_MODEL` 配置的模型在当前账号未开通或不存在。模型名因账号而异（如 `qwen3-reranker-4b` / `qwen3-reranker-8b`，部分账号仅 `qwen3-rerank`）；请到百炼控制台模型广场搜索确认可用模型名后更新 `.env`，或设 `RERANKER_ENABLED=false` 关闭重排（自动降级本地 TF-IDF，功能不受影响）。

---

## 许可证

本项目仅供学习和研究使用。