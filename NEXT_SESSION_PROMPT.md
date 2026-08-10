# 新窗口接续 Prompt（2026-08-10 生成）

> 用法：将以下内容完整粘贴到新窗口作为第一条消息。

---

你是一个在以下项目中工作的全栈工程师/测试工程师。请先读取关键文件建立上下文，再开始工作。

## 项目概况

- **项目**：文物/展会知识库 RAG 问答系统（多项目架构：museum / enterprise / jiabohui），含 Gradio Web UI、语音输入（讯飞 IAT WebSocket ASR）与语音播报（百炼 CosyVoice TTS，句子级流式）。
- **路径**：`E:/project/agent_project/pi/test`（Windows 环境，工作目录即项目根目录）。
- **技术栈**：Python 3.10、阿里云百炼（qwen-plus LLM、text-embedding-v4、qwen3-rerank 重排）、Qdrant（本地持久模式）、BM25+向量 RRF 混合检索、Gradio 6.22（注意：必须 starlette<1.4 + fastapi<1.0，见 requirements.txt 注释）、pytest。
- **代码结构**：`app.py`（Web UI + ASR/TTS 编排）、`src/`（config/cache/embeddings/vector_store/retriever/reranker/llm/rag_pipeline/intent_classifier/chunking/data_loader/document_loader/project/utils/asr/tts/audio_bootstrap）、`scripts/`（构建与 QA 工具）、`tests/`（9 个测试文件）。

## 当前状态（以此为最新事实）

- **测试：505 passed, 0 failed**（`python -m pytest tests/ -q`，全程离线约 20s）。
- **最新有效审查文档：`code_review_report_v3.md`**（2026-08-10 全面审查 + 修复终态）。
  `code_review_report.md` / `code_review_report_v2.md` / `remaining-issues.md` / `unfixed-impact-analysis.md` 均为**历史文档**，与 v3 冲突时以 v3 为准。
- 2026-08-10 完成第十二轮修复（audit-F1~F27，详见 v3 报告第八节与 README 更新日志 v1.3.5-pre）。
- `.env` 含真实密钥（已 gitignore，不要打印/提交）；`.env` 键名已修正为 `EMBEDDING_MODEL_NAME`。

## 必须遵守的既有决策（不要重新"修复"）

1. **模式缓存放宽匹配**：含否定词的问题（如"我不推荐…"）命中模式缓存返回他句 embedding 是 bug-006 的既定可接受妥协（近似 embedding 优于缓存未命中；下游有重排+LLM 相关性闸门）。有测试固化（TestEmbeddingCacheBoundaryBug / TestEmbeddingCache）。
2. **ASR finalized 后忽略后续音频块**：防止重建会话导致无限识别（TestAsrGuards 固化）。stop_recording 事件未到达时新录音首块可能被忽略——已知低风险，维持现状。
3. **`is_kb_related("谢谢你的帮助") == True`**：剥离"谢谢"后剩"你的帮助"有实质内容，路由知识库是既定契约（TestIsKBRelatedFalsePositives）。纯感谢（"谢谢你/感谢您"）判闲聊（语气词含"你/您"）。
4. **RRF 量纲判定阈值 0.02**：RRF 理论上限 1/61≈0.0164（rrf_k=60，权重和 1），重排分（0~1）低于 0.1 是合法低分，不得再用 0.1 阈值猜测量纲。
5. **rerank 单候选必须走 API**：pipeline 的相关性闸门（RELEVANCE_THRESHOLD=0.45）只认 0~1 重排分，提前返回 RRF 分会误判拒答。
6. **检索故障不写缓存**：任一侧（语义/BM25）失败的结果不写入 retrieval_cache。
7. **Gradio 6 兼容分支**：Chatbot dict 消息格式、theme/css 移到 launch()、buttons=["copy"]——按 `_GRADIO_MAJOR` 分支的既有模式，不要破坏 4/5/6.x 兼容。
8. **dashscope 流式必须 `incremental_output=True`**（否则累积全文 chunk 导致内容重复膨胀）。

## 工程纪律（本项目惯例）

- 修复注释打标签：历史用 `bug-xxx`，本轮起用 `audit-Fxx`；新 bug 顺延编号。
- **TDD**：先写/改测试复现，再改源码，最后全量回归；测试必须离线可跑（外部 API 一律 mock）。
- 改动后运行 `python -m pytest tests/ -q` 确认 505+ 全绿；提交前不要留失败测试（长期 RED 的测试要么修要么 xfail 并注明）。
- 文档表述以最新为准：修改行为后同步更新 README 更新日志与 code_review_report_v3.md。

## 待办（下一轮候选，按优先级）

1. `code_review_report_v3.md` 第六节「边界情况覆盖缺口」：embedding 构建期全败的部分失败状态、chunks.json 与 Qdrant 集合不一致自愈、Excel 合并单元格/多级表头、wav 8/24bit、ASR 断线重连、超长文档分段句子硬切。
2. 性能观察项（v3 报告第四节）：ASR 回调内 sleep(0.2) 阻塞、每查询新建线程池、BM25 全量重建、纯 Python 余弦/混音。
3. 公网部署前：UI 认证/限流（当前无防护，`--share` 或 0.0.0.0 时任何人可消耗 API 额度）。

## 开始工作前

1. `git log --oneline -3` 与 `git status` 确认工作区状态；
2. 读 `code_review_report_v3.md` 第八节（修复终态）建立最新事实；
3. 跑 `python -m pytest tests/ -q` 确认 505 全绿基线；
4. 然后向我确认本次任务目标。
