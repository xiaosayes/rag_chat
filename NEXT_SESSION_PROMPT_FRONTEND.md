# 新窗口接续 Prompt — 数字人前端开发（2026-08-14 生成）

> 用法：将以下分隔线之间的内容完整粘贴到新窗口作为第一条消息。
> 本 prompt 只基于 **main 分支**事实编写（main HEAD = d3c9116，测试基线 637 passed 已实测复跑确认）。

---

你是一个在以下项目中工作的全栈工程师。请先按「第 0 步」建立上下文，再开始工作。本次任务是**开发全新的数字人前端**（一体机/大屏等终端），后端服务已完成并冻结。

## 0. 开始工作前（必须先做）

1. `git log --oneline -3` 与 `git status` 确认工作区状态；当前应在 `main`（HEAD=`d3c9116`）。
2. 读 `README.md` 更新日志 v1.5.0/v1.4.0 两节（语音助手与 TTS 播报的最终行为事实）；
   读 `NEXT_SESSION_PROMPT.md`（后端交接 prompt，含 8 条既定决策，本 prompt 第 6 节已摘录）；
   最新有效审查文档是 `code_review_report_v3.md`（第十节=语音助手 audit-ASR，第九节=TTS audit-TTS）。
3. 跑 `python -m pytest tests/ -q` 确认 **637 passed** 基线（全离线，约 31s）。
4. **工作区卫生提示**：工作区根目录若存在 `web/`（仅 dist/node_modules）、`src/services/__pycache__` 等，
   是历史残留，**不属于 main 分支**，不要当作既有成果读取或依赖。
5. 然后与我确认本次任务目标，**等我提供：①类似数字人项目的前端实现源码（参考）；②UI 设计稿**。
   未拿到这两份材料前不写 UI 代码。

## 1. 项目背景

- **项目**：文化知识库 RAG 问答系统（多项目架构：museum / enterprise / jiabohui），
  路径 `E:/project/agent_project/pi/test`（Windows 开发机，工作目录即项目根目录）。
- **技术栈**：Python 3.10、阿里云百炼（qwen-plus LLM、text-embedding-v4、qwen3-rerank）、
  Qdrant（本地持久模式）、BM25+向量 RRF 混合检索、讯飞 IAT v2 WebSocket ASR、
  百炼 CosyVoice TTS、Gradio 6.22（**starlette 必须 <1.4、fastapi <1.0**，见 requirements.txt 注释）、pytest。
- **生产现场**：Linux 服务器（ub-server）`/data/codes/rag_chat`，conda 环境 `cultural-relics-rag`，
  运行 jiabohui（家博会数字人「小虎」）项目，Gradio 服务 `:7860`（`python app.py --project jiabohui --host 0.0.0.0 --port 7860`）。
  知识库 171 切片已用 text-embedding-v4 构建；重排走 qwen3-rerank API（已实证生效）。

## 2. main 分支最新事实快照（以此为最新事实）

- **HEAD**：`d3c9116`（docs: 删除《数字人一体机落地方案.md》——该文档已按用户要求从本地与 origin 删除；
  **github 远端推送因网络问题未成功，网络恢复后需补 `git push github main`**，共 17 个提交）。
- **版本线**：v1.5.0 语音助手（audit-ASR）收官 → 测试基线 **637 passed**（0 失败，全离线）。
- **后端三大能力全部完成并优化完毕**：
  - **RAG 问答**：分层意图分类（L0 规则闲聊路由 → L1 向量语义 → L2 LLM 兜底）→ 并行混合检索
    （语义+BM25，RRF 0.7/0.3 融合）→ qwen3-rerank 精排 → qwen-plus 流式生成
    （`incremental_output=True` 强制）→ 防幻觉检查；三层缓存（Embedding 持久化/LLM 30min/检索 5min）；
    多轮对话保留 4 轮；闲聊首字 ~300-500ms、知识库流式首字 ~1-2s。
  - **ASR 语音输入**：讯飞 IAT v2 WebSocket 流式（wpgs 边说边出字，增量 <500ms），16k PCM；
    silero VAD（`src/vad.py` 自持 ONNX 推理，模型内置 `src/assets/silero_vad.onnx`）；
    纠词典 `data/voice/asr_dict.json`（**已跟踪入库**，全局 + 项目级覆盖，支持 `[{from,to}]` 新格式）。
  - **TTS 语音播报**：百炼 `cosyvoice-v3-flash`；**单会话流式合成**（LLM 文本增量喂入，
    句边界批量喂 + `_PauseCompressor` 静默压缩），`_AdtsStreamer` 单 ffmpeg 持续 AAC 编码按帧界切片，
    浏览器 HLS 播放；语速 1.1x（`TTS_SPEECH_RATE`）；首播 TTS 侧 ~1.0s（物理下限 ≈1.1s）；
    标准段 2.0s、首播爬坡 0.4/0.6/0.8s；流式看门狗 15s（重建 ≤2 次）；重播走原始 PCM。
  - **语音助手（audit-ASR，默认关，`VOICE_ASSIST_ENABLED=true` 开）**：四态状态机
    standby/await_broadcast/broadcast/listen（`src/voice_assistant.py`，纯逻辑零 gradio 依赖）；
    唤醒词默认「你好小虎」（`.env ASR_WAKE_WORDS`，`asr_dict.json` 的 `wake_words`/`wake_greeting` 项目级覆盖）；
    应答语「您好，我是小虎，请问有什么可以帮您？」合成一次内存+磁盘缓存复用，
    并经 `GET /__voice_greeting` 预置静态文件直发（词尾→应答 ~0.3s）；
    双计时：初始倾听窗 8s（`ASR_INITIAL_WAIT_S`）+ 每段语音后延长 2s（`ASR_EXTEND_WAIT_S`，2s 静默自动提交）；
    播报中 ≥400ms 持续语音即打断（按 session_hash 定位播报 token，0.1s 拍检查 cancel）；
    VAD 参数：`VAD_THRESHOLD=0.5 / VAD_MIN_SPEECH_MS=400 / VAD_MIN_SILENCE_MS=500 / VAD_SPEECH_PAD_MS=200 / VAD_MAX_SPEECH_S=15`；
    **防自触发双保险**：浏览器录音强制 AEC（echoCancellation/noiseSuppression/autoGainControl）+
    播报期间只跑 VAD 不送 ASR。

## 3. 本次任务定义

1. **开发全新的数字人前端**（独立 SPA，不用 Gradio）：**高保真还原我提供的 UI 设计稿**，
   参考我提供的类似数字人项目前端源码。
2. **部署目标**：Windows 一体机（竖屏 Chrome kiosk）、大屏及其他终端；前端跑在终端上，
   **后端服务（ASR/TTS/RAG 问答）全部继续部署在服务器上**，前端通过网络调用。
3. 终端/kiosk 惯例（main README 已固化）：Chrome 启动参数
   `--kiosk --autoplay-policy=no-user-gesture-required --use-fake-ui-for-media-stream
   --disable-pinch --overscroll-history-navigation=0`；
   无人值守免麦克风授权弹窗靠 `--use-fake-ui-for-media-stream`。

## 4. 红线约束（最高优先级，违反即返工）

1. **后端冻结**：`src/` 全部内核（rag_pipeline/asr/tts/vad/voice_assistant/retriever/llm 等）、
   `app.py`、`.env` 及服务器部署**一律不准修改**；后端已优化完成，
   **任何服务端改动必须先提出方案并经我显式确认**。
2. **架构事实**：main 上后端能力目前编排于 Gradio `app.py` 内部，**没有面向独立前端的
   REST/WebSocket API**。前端对接后端需要新增服务端薄层（API/WS）时，只允许**新增文件**
   （复用 `src/` 内核，零改动既有代码），且**方案必须先报我确认**再实施。
3. **密钥安全**：`.env` 含真实密钥（已 gitignore），不打印、不提交、不进前端代码；
   讯飞/百炼密钥只允许留在服务端。
4. **分支纪律**：从最新 `main` 切新分支开发（分支名先与我确认）；不直接提交到 main；
   合并回 main 由我手动执行。
5. **测试纪律**：TDD；测试全离线可跑（外部 API 一律 mock）；每次改动后
   `python -m pytest tests/ -q` 保持 **637+ 全绿**（前端自建的前端测试不计入该数，另算）。

## 5. 工程纪律（本仓库惯例）

- 修复/功能注释打标签：历史用 `bug-xxx`、`audit-Fxx`、`audit-ASR/TTS`；
  本轮前端工作标签与提交前缀（如 `feat(web):`）开工时与我确认。
- 改动行为后同步更新 README 更新日志；审查结论累加进 `code_review_report_v3.md`。
- `.gitignore` 惯例：`docs/` 与 `data/` 被忽略（`data/voice/asr_dict.json` 例外已跟踪）。
  **我提供的参考源码与 UI 设计稿建议放在 `data/` 之下（如 `data/front_ui/`），天然不入库。**
- Gradio 6.22 依赖约束不可破坏：starlette<1.4 + fastapi<1.0。
- dashscope 流式必须 `incremental_output=True`。

## 6. 既有决策（后端侧，不要重新"修复"）

1. 模式缓存放宽匹配（含否定词问题命中模式缓存返回近似 embedding）是 bug-006 既定妥协，有测试固化。
2. ASR finalized 后忽略后续音频块（防无限识别，TestAsrGuards 固化）。
3. `is_kb_related("谢谢你的帮助")==True` 是既定契约；纯感谢才判闲聊。
4. RRF 量纲判定阈值 0.02（理论上限 1/61≈0.0164）；重排低分（<0.1）是合法低分。
5. rerank 单候选必须走 API（相关性闸门只认 0~1 重排分）。
6. 检索故障不写缓存（任一侧失败的结果不进 retrieval_cache）。
7. Gradio 6 兼容分支（dict 消息格式、theme/css 移到 launch()、buttons=["copy"]）勿破坏 4/5/6.x 兼容。
8. 语音助手关闭（VOICE_ASSIST_ENABLED=false）时手动模式行为零变化。

## 7. 开工流程

1. 完成第 0 步（环境/基线确认）；
2. 接收我提供的参考前端源码与 UI 设计稿，先产出**技术分析 + 实现计划**
   （技术栈以参考源码为准；设计稿逐张盘点组件/布局/交互/动效），报我确认后再动工；
3. 需要服务端薄层 API 时，先出接口设计（端点/协议/数据契约）报我确认；
4. 实现阶段每步 TDD，保持三端事实同步：代码 / 测试 / README；
5. 完工前亲自运行命令验证（不凭推断下结论），并向我汇报实测证据。

---

（分隔线以上内容粘贴到新窗口）
