# 代码审查报告 v3（2026-08-10，测试工程师视角）

> **状态：已全部修复完成（2026-08-10）**。最终测试：**505 passed, 0 failed**。
> 各发现的处理结果见文末「修复执行结果」；本文档为最新有效审查文档，
> 此前的 code_review_report.md / code_review_report_v2.md / remaining-issues.md /
> unfixed-impact-analysis.md 均为历史文档，内容如与本文冲突以本文为准。

审查方式：从零重读全部源码（src/ 17 个模块 + app.py + scripts/），不假设任何代码正确；
对每个疑点先写最小复现验证，再将确认的缺陷固化为可执行测试（`tests/test_audit_fresh_review.py`）。

- 基线（审查前）：467 passed, **2 failed**（test_edge_cases.py，bug-117b 遗留）
- 审查后（修复前）：473 passed, **21 failed**（19 个新增测试 = 13 个新确认缺陷的直接证据 + 2 个基线遗留）
- **修复后（当前）：505 passed, 0 failed**
- 所有测试离线运行，不依赖外部 API。

---

## 一、P0 高危缺陷（数据丢失 / 功能失效 / 用户可见错误）

### F1. Excel 首行为空时整表数据静默丢失（data_loader.py `_load_xlsx`）
- **现象**：表头识别用 `if not header:` 判断。首行全空时 `header=["",""]`（非空列表，判 False），
  表头被置为全空串；后续真正的表头行和数据行全部因"列名为空"被跳过 → **整个 sheet 0 条记录，无任何告警**。
- **影响**：真实 Excel 常有前导空行/标题行 → 知识库构建静默丢数据。
- **测试**：`TestXlsxBlankFirstRow::test_blank_first_row_should_not_lose_data`
- **修复建议**：改用 `header is None` 标志位判断；跳过全空行直到遇到首个非空行作为表头。
- **附带**：`row_idx` 是死变量；docstring 承诺的"{sheet名}第N行"兜底命名从未实现。

### F2. ASR 音频容器魔数检测 3 处错误（asr.py `_is_encoded_container`）
- **现象**：`audio_bytes[:4] in magics`，但：
  1. `b"ftyp"` —— MP4/M4A 的 `ftyp` 在**偏移 4**（前 4 字节是 box size），偏移 0 永不命中；
  2. `b"ID3"` —— 仅 3 字节，与 4 字节切片比较**永不命中**；
  3. `b"\xff\xfb"` —— 仅 2 字节，同样**永不命中**。
  实际只有 webm(EBML) 和 ogg(OggS) 两个 4 字节魔数生效。
- **影响**：Safari 录音（mp4/m4a）、MP3 音频全部被当裸 PCM 送讯飞 → 识别输出乱码。
- **测试**：`TestAsrContainerMagic::test_mp4_ftyp_at_offset4_detected`、`test_webm_ogg_mp3_still_detected`
- **修复建议**：按魔数长度分别比较（`startswith`），mp4 检查 `audio_bytes[4:8] == b"ftyp"`。

### F3. ASR 异常帧使接收线程静默死亡（asr.py `_handle_message`）
- **现象**：`w["cw"][0]["w"]` 遇 `cw: []` 或缺键帧 → `IndexError/KeyError`。
  该方法在 `_recv_loop` 线程中**无 try/except** 调用 → 异常直接杀死接收线程，
  此后不再接收任何结果，`finish()` 空等 10s 超时返回残缺文本。
- **测试**：`TestAsrMalformedFrame::test_empty_cw_does_not_crash`、`test_missing_cw_key_does_not_crash`
- **修复建议**：`_handle_message` 内对单帧解析加防御（`w.get("cw") or [{}]`），异常单帧跳过而非杀线程。

### F4. 检索瞬时故障结果被写入缓存（retriever.py `HybridRetriever.retrieve`）
- **现象**：语义/BM25 子任务失败仅记日志，结果照常（不完整/空）写入 `retrieval_cache`（TTL 300s）。
  复现：第 1 次语义 API 故障 → 只返回 BM25 结果；第 2 次语义已恢复 → 仍命中缓存返回旧结果。
- **影响**：一次抖动被固化 5 分钟；空结果缓存导致该问题 5 分钟内永远"知识库无内容"。
- **测试**：`TestRetrieveDoesNotCacheFailures`（2 个用例）
- **修复建议**：任一子任务异常时跳过 `retrieval_cache.set`。

### F5. rerank 单候选提前返回 RRF 分，被当 0~1 相关性分（reranker.py + rag_pipeline.py）
- **现象**：`rerank()` 对 `len(candidates) <= 1` 直接原样返回（分数仍是 RRF 量级 ~0.008），
  但 pipeline 置 `reranked=True` → `_has_relevant_results` 用 `RELEVANCE_THRESHOLD=0.45` 比较 → 必然 < 阈值。
- **影响**：时效性问题恰好检索到 1 条结果时：开启 LLM 相关性确认 → 每次多一次无意义 LLM 调用（费用）；
  关闭确认（`LLM_RELEVANCE_CHECK_ENABLED=false`）→ **直接误判"知识库无信息"返回委婉拒答**。
- **测试**：`TestRerankSingleCandidateScoreScale`
- **修复建议**：单候选也走 API 重排，或在 pipeline 侧以"是否真正重排"而非调用与否置 `reranked`。

---

## 二、P1 中危缺陷

### F6. VectorStore.search 单条坏 payload 杀死整个语义检索（vector_store.py）
- `json.loads(payload.get("metadata_json", "{}"))`：值为 JSON null → `TypeError`；坏串 → `JSONDecodeError`。
  异常在循环内未捕获 → 整个 search 失败 → 上游 retriever 捕获后**语义检索静默为空**（只剩 BM25）。
- 测试：`TestVectorStoreSearchRobustness`（2 个用例）。建议：单点解析失败跳过该点并记 warning。

### F7. VectorStore 关闭后 client 静默返回 None（vector_store.py）
- `close()` 后 `client` 属性返回 None → 下游 `search/upsert/get_stats` 报 `'NoneType' object has no attribute ...`（晦涩）。
- 附带：`reset_connection()` 在锁外把 `_closed` 置回 False，存在竞态窗口（另一线程此刻拿 client 得到 None）。
- 建议：closed 状态访问 client 直接 `raise RuntimeError("VectorStore 已关闭")`。

### F8. embedding batch_size ≤ 0 未防御（embeddings.py）
- 仅钳制 >10 和非 int；`batch_size=0` → `range(step=0)` ValueError；负数 → 全部批次缺失 RuntimeError。
- 测试：`TestEmbeddingBatchSizeNonPositive`。建议：`batch_size < 1` 时钳制为 1 或默认值。

### F9. 基线遗留：2 个测试一直失败（bug-117b 控制字符清洗未实现）
- `TxtParser`/`load_directory` 未做任何 C0 控制字符清洗，测试期望清洗 → 测试长期 RED。
- **另注意**：`test_load_directory_applies_cleaning` 断言 `"杂字符" not in doc.content` —— 该断言本身矛盾
  （清洗控制字符不应删除正常文字），测试用例也需要修正。
- 建议：要么在 `TxtParser.parse` 补清洗逻辑，要么给测试标 xfail 并修断言；保持测试常绿纪律。

### F10. format_answer 把 <0.1 的重排低分误判为 RRF 量纲（app.py）
- `rrf_scale = 0 < max_score < 0.1`：重排分（0~1）本来就可能是 0.05 的低相关分 → 被当 RRF 量纲，
  按排名把第 1 名标 **[高]**（应为 [低]），误导用户。
- 测试：`TestFormatAnswerScoreScale`。建议：用 `reranked` 标志而不是分数量纲猜测。

### F11. `_select_prompt` 缺少 CHITCHAT 键（rag_pipeline.py）
- `prompt_type_map`/`system_prompt_map` 均无 `QueryType.CHITCHAT` → KeyError。
  当前 query() 在主流程把 CHITCHAT 改写为 UNKNOWN 而**碰巧不可达**，但属脆弱 latent crash。
- 测试：`TestSelectPromptChitchatKey`。建议：映射补 CHITCHAT → "chitchat"/default。

### F12. TTS 重播文件为全局单文件，多用户互相覆盖（app.py `_write_replay_wav`）
- 所有会话共用 `data/processed/tts_cache/last_answer.wav`：用户 A 的"重播"会播放到用户 B 的答案
  （并发互相覆写）。多用户部署下是串音 + 轻微隐私问题。
- 建议：按会话/请求 ID 命名（如 `last_answer_{uuid}.wav`），或存内存不落地。

---

## 三、P2 低危 / 隐患 / 一致性问题

| # | 位置 | 问题 |
|---|------|------|
| F13 | data_loader.py `Artifact.to_text` | 非字符串 tags（JSON 数字列表）→ `join` TypeError。当前未被调用（死代码带隐患）；chunking 已修（bug-090）此处漏修 |
| F14 | utils.py `clean_text_for_tts` | 货币 `$5` 在前、公式 `$x^2$` 在后时，`\$...\$` 正则把货币$到公式$配对吞掉 → 公式残留裸 `x^2$` 进 TTS。测试：`TestTtsCleanDollarLatex` |
| F15 | rag_pipeline.py `is_kb_related` | L0 漏判"谢谢你""感谢你的帮助"（"谢谢"剥离后剩"你"非语气词）。L1 语义层可兜底；但 `INTENT_SEMANTIC_ENABLED=false` 时会走完整 RAG 检索+LLM。测试：`TestIsKbRelatedThanks` |
| F16 | .env | `EMBEDDING_MOD_NAME` 拼写错误（少 `EL`），pydantic `extra="ignore"` 静默忽略。当前恰好等于默认值无影响，改配置时是地雷 |
| F17 | reranker.py `_rerank_local` | 空 query + 全空文本 → TF-IDF `empty vocabulary` ValueError，穿透 `rerank()` 的降级保护。主流程 query 非空故**当前不可达**（latent）。测试：`TestRerankLocalEmptyVocab` |
| F18 | app.py `tts_after_answer` | 定义后从未被任何事件绑定（死代码，旧版播报路径残留） |
| F19 | cache.py `EmbeddingCache` | 模式命中返回的是**另一句话**的向量：否定句"我不推荐…"命中"推荐…"模式 → 语义反转（代码注释中声明接受的妥协，建议至少对含否定词的查询跳过模式缓存） |
| F20 | document_loader.py `PptxParser` | 声称支持 `.ppt`，但 python-pptx 不支持旧格式必然抛错（`DocxParser` 对 `.doc` 有友好降级，二者不一致） |
| F21 | project.py `_load_projects` | 启动加载外部 JSON 时不校验 `id`（`add_project` 有 `[A-Za-z0-9_-]+` 校验）→ `id` 含 `../` 时 `data_dir` 路径穿越。利用前提是能写 data/projects/，残余风险低 |
| F22 | app.py `launch(show_error=True)` | 向前端用户泄漏完整堆栈；UI 无认证/限流，`--share` 或绑定 0.0.0.0 时任何人可消耗你的 API 额度 |
| F23 | intent_classifier.py `classify_with_llm` | 子串匹配忽略否定：LLM 输出 "not chitchat" 会命中 "chitchat"。建议先精确匹配再子串 |
| F24 | rag_pipeline.py `GREETING_WORDS` | 子串"在吗"误伤"存在吗"（UNKNOWN 类问题被禁联网）；英文 chitchat 整体覆盖弱（"hi" 从 "this" 中剥离） |
| F25 | cache.py `EmbeddingCache.save` | 直接覆写 JSON，非原子写（崩溃 → 缓存文件损坏；加载端有兜底，但缓存全丢）。建议 tmp+rename |
| F26 | document_loader.py `load_file` 注释 | 注释声称"bug-023 修复：防止路径遍历"，实际只 `resolve()` 无归属校验——注释过度承诺 |
| F27 | app.py `asr_stream_chunk` | `state["finalized"]` 后若 `stop_recording` 事件未到达，state 残留 → 下一次录音首块被忽略（依赖 gradio 事件时序，建议新一轮录音检测后自动重置 state） |

## 四、性能观察（非 bug，按影响排序）

1. **app.py `asr_stream_chunk` 内 `time.sleep(0.2)`**：阻塞 gradio 事件处理线程，每音频块 200ms；高并发录音时累积。
2. **`HybridRetriever.retrieve` 每查询新建 `ThreadPoolExecutor`**：2 线程池创建/销毁开销（~1ms 级），建议模块级复用。
3. **L1 意图分类每问一次 embedding + 57 原型纯 Python 余弦**：embedding 有缓存兜底，但新问题=1 次 API 调用；余弦可 numpy 向量化（量级小，影响低）。
4. **`add_artifacts` 全量重建 BM25**：语料大时增量添加成本高（rank_bm25 不支持增量）。
5. **`_downmix_to_mono` 纯 Python 循环**：48kHz 立体声 1s ≈ 4.8 万次循环，可 numpy 化。
6. **`EmbeddingCache.get` 模式匹配**：未命中时对全部模式做子串扫描（模式少，影响低）。

## 五、安全审计结论

✅ 正面：`.env` 已 gitignore 且未入库；缓存用 JSON 不用 pickle；无 `eval/exec/shell=True`；
`Settings.__repr__` 屏蔽密钥；`add_project` 校验项目 ID；ffmpeg 子进程列表传参无注入。

⚠️ 残余：F21（项目 id 校验不一致）、F22（show_error + 无认证/限流）、F12（重播文件串用户）、
F26（防路径遍历注释过度承诺）。部署公网前需处理 F22。

## 六、边界情况覆盖缺口（现有测试未覆盖）

- embedding API 构建期全部失败时 `build_knowledge_base` 的部分失败状态（chunks.json 已写、Qdrant 空）
- chunks.json 与 Qdrant 集合不一致（集合被删但缓存还在 → 语义静默降级）—— 仅记 warning，无自愈/无测试
- Excel：合并单元格、多级表头、表头在 3+ 行、全空 sheet
- wav 8bit/24bit 位深（当前直接 raise，无测试）
- ASR 断线重连、finish() 后再 feed()
- 多用户并发 TTS 重播（F12）
- 超长文档分段在句子中间硬切（无重叠窗口）
- `_trim_context` 单段超长截断后的 LLM 输入质量

## 七、修复优先级建议

1. **F1**（Excel 数据静默丢失）、**F2/F3**（语音输入在 Safari/mp3 失效 + 线程静默死亡）—— 直接用户可见
2. **F4**（故障缓存 5 分钟）、**F5**（单候选误判拒答）—— 正确性
3. **F6/F7/F8**（鲁棒性防御）、**F9**（测试常绿 + 修正矛盾断言）
4. **F10/F11/F12** 及其余 P2

新增测试文件：`tests/test_audit_fresh_review.py`（36 个用例：缺陷证据 + 修复后回归保护）。

---

## 八、修复执行结果（2026-08-10，最终状态）

**全量测试：505 passed, 0 failed**（基线 467 passed / 2 failed）。
变更：13 个源码文件 + 3 个测试文件修改，新增 `tests/test_audit_fresh_review.py`。

### 已修复（20 项）

| 编号 | 修复内容 | 文件 |
|------|---------|------|
| F1 | xlsx 表头识别改 None 标志位 + 跳过前导空行（整表静默丢失修复） | `data_loader.py` |
| F2 | ASR 容器魔数：`ftyp` 查偏移 4；`ID3`(3B)/`\xff\xfb`(2B) 按实际长度比较 | `asr.py` |
| F3 | ASR 逐词防御性解析 + `_recv_loop` 异常保护（坏帧不再杀接收线程）；`ls` 终帧独立判断 | `asr.py` |
| F4 | 任一侧检索故障时不写 `retrieval_cache`（瞬时故障不再固化 5 分钟） | `retriever.py` |
| F5 | rerank 单候选走 API 拿真实 0~1 相关性分（不再把 RRF 分当相关性分误判拒答） | `reranker.py` |
| F6 | search 单点 `metadata_json` 为 null→容忍为 {}，坏串→跳过该点（不再整条检索崩溃） | `vector_store.py` |
| F7 | close 后访问 client 抛清晰 RuntimeError；`reset_connection` 锁内复位 `_closed` | `vector_store.py` |
| F8 | `batch_size < 1` 钳制为默认值（0/负数不再崩溃） | `embeddings.py` |
| F9 | `load_file` 统一清洗 C0 控制字符（保留 \n\t\r），bug-117b 落地；同时修正了原测试中自相矛盾的断言（原断言要求清洗后删除正常文字） | `document_loader.py` + `tests/test_edge_cases.py` |
| F10 | RRF 量纲阈值 0.1→0.02（RRF 理论上限 1/61≈0.0164），重排低分不再误标[高]；同步修正 test_emoji_filter 中超出 RRF 理论上限的夹具分数 | `app.py` + `tests/test_emoji_filter.py` |
| F11 | `_select_prompt` 补 CHITCHAT 映射（消除潜在 KeyError） | `rag_pipeline.py` |
| F12 | TTS 重播文件按请求唯一命名 + 仅保留最近 5 个（多用户不再互相覆写） | `app.py` |
| F13 | `to_text` 非字符串 tags 统一转 str（与 chunking bug-090 一致） | `data_loader.py` |
| F14 | LaTeX 起始 `$` 加负向断言排除货币 `$5`（公式不再残留裸 `x^2$`） | `utils.py` |
| F15 | 闲聊语气词补「你/您」（"谢谢你/感谢您"判闲聊；"谢谢你的帮助"维持前轮固化的 True 契约） | `rag_pipeline.py` |
| F16 | `.env` 键名拼写 `EMBEDDING_MOD_NAME` → `EMBEDDING_MODEL_NAME` | `.env` |
| F17 | 本地重排全空文本提前返回；`rerank()` 双层降级不再穿透抛异常 | `reranker.py` |
| F20 | `.ppt` 旧格式告警 + 纯文本兑底（与 `.doc` 处理一致） | `document_loader.py` |
| F21 | 外部项目 JSON 的 id 启动加载时同样校验（与 `add_project` 一致，防路径穿越） | `project.py` |
| F22 | `show_error` 仅 DEBUG 日志级别开启（生产不再向前端泄漏堆栈） | `app.py` |
| F23 | LLM 意图分类否定表述（"not chitchat"）不误命中子串，返回 None 走规则层 | `intent_classifier.py` |
| F24 | 问候词改边界匹配（"存在吗"不再误伤"在吗"） | `rag_pipeline.py` |
| F25 | 缓存 tmp + os.replace 原子写（崩溃不再损坏缓存文件） | `cache.py` |
| F26 | `load_file` 防路径遍历注释过度承诺→如实标注无归属校验 | `document_loader.py` |
| F18 | `tts_after_answer` 标注「UI 未接线，保留供测试/外部脚本」 | `app.py` |

### 评估后维持原决策（2 项，代码注释记录依据）

| 编号 | 决策 |
|------|------|
| F19 | 含否定词问题命中模式缓存：bug-006 已明确「近似 embedding 优于未命中」并有两处测试固化（TestEmbeddingCacheBoundaryBug/TestEmbeddingCache），维持原行为 |
| F27 | ASR finalized 后忽略后续块：TestAsrGuards 明确防护「finalized 后继续 feed 导致无限识别」，两害相权维持；stop_recording 事件未到达的极小概率残留风险已注释记录 |

### 未处理项（留待后续）

- 性能观察项（第四节）：均为非 bug，按需优化
- 第六节「边界情况覆盖缺口」清单：作为下一轮测试补充输入
- F22 补充：公网部署前需自行加认证/限流（非代码缺陷，部署侧责任）

---

## 九、第十三轮：TTS 播报重做（audit-TTS，2026-08-10 深夜）

**验收标准（用户下达）**：首句播报 ≤1s；全程无停顿；第 2 轮起播报正常。
**方法**：系统化调试（根因优先）+ 全程真实浏览器 E2E（playwright + 真实 API），
所有根因均有日志/网络时间线/源码实证，非推测。

### 验收结果（E2E 实证，`scripts/e2e_tts_browser.py`）

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 首句延迟（首文本→开播） | ~3s | **0.65~1.0s**（含编码器探针修复后实测） |
| 中途停顿 | 实测 22~58s 断流（多发） | **两轮零停顿**（mock-LLM 隔离模式与真实全链路均验证） |
| 第 2 轮播报 | 无声 | **正常**（开播于发送后 ~1.1s） |
| 音质 | 段边界 priming 吞音（听感发闷） | **连续编码流，接口无缝**（实测接口 RMS 与整体一致） |
| 前向缓冲水位 | ~0.1s | p50 30~60s |
| 发布吞吐 | 0.54x 实时（146.7s 音频 269.7s） | ~3x 实时（96s 音频 ~33s） |

### 音质根因与修复（同轮第二轮修复，用户反馈“听感发闷、不是字正腔圆”）

10. **段边界 priming 吞音**：逐批独立编码的每个 AAC 段带 ~43ms priming 头静音 + ~24ms
    拖尾（实测：段解码 42.7ms 才有声、接口能量塌至 1/3；剥首帧实验仅部分改善）——
    0.9s 段即每 0.9s 一次吞音 → **单 ffmpeg 进程持续编码**（`_AdtsStreamer`：PCM 写
    stdin，stdout 连续 AAC 流按帧界切片），编码状态跨段连续，接口彻底无缝；
    `_convert_to_adts` patch 增加 **ADTS 直通**（切片不再二次编码，避免代际损失与
    priming 回插）；码率显式 `-b:a 96k`。
11. **编码器探针缓冲致首播回退**：原始 PCM 流无头可探，ffmpeg 默认 probesize 会攒满
    探针缓冲才开工（实测首字节 >700ms）→ `-probesize 32 -analyzeduration 0`
    （首字节 ~210ms）+ stdout `read1`（read 会等满 4KB 缓冲才返回）。
12. **重播质量无损**：重播文件改由原始 PCM 直接打 wav（不再复用有损/病态风险各异的
    已发布段），与编码器健康完全解耦。

### 第三轮（语速 + 中途停顿加固）

13. **语速 +10%**：`speech_rate=1.1`（`TTS_SPEECH_RATE`），流式/非流式均透传
    （实测时长比 0.909 = 精确 1/1.1）。
14. **中途停顿根因定论与加固**：`scripts/tts_starve_probe.py` 实证断粮不烙停顿
    （整段喂/断粮 2.5s/仅拆分三组音频逐字节一致）→ 用户感知的 2-3s 中途停顿是
    播放器缓冲被 LLM 出文停顿耗空的断流，非音频内容问题。加固：标准段 0.9s→2.0s
    （高 RTT 客户端下拉 playlist→拉段串行周期 ~2×RTT/段，0.9s 段追不平消费）；
    首播爬坡 0.4/0.6/0.8 不变，TD patch clamp 到 2。
15. **缓冲告急诊断**：respond 估算剩余缓冲（已发布音频 − 开播至今墙钟），<1.5s 且
    PCM 断流 >1s 时输出告警日志（含客户端 patch 排查指引），生产复测可据此定位。
16. **客户端停顿遥测**：`launch(head=)` 注入探针，video waiting→playing ≥0.8s 自动
    sendBeacon `/__tts_stall`（`_TtsStallBeaconMiddleware` ASGI 应答 + WARNING 日志：
    时长/播放位置/前向缓冲）。ahead≈0→发布/网络追不平；ahead>3→播放器侧。全链路
    实证：合成事件 → 探针捕获 → 信标落日志（scripts/verify_stall_beacon.py）。
17. **前端 patch 启动自检**：`verify_frontend_patches()` 复读磁盘 JS 确认三标记落盘，
    失败打 ERROR。生产日志实证（用户服务器 02:19 轮）：发布 2.7x 实时、重建=0、
    零告急零断流 → 服务端发布链健康。
18. **中途停顿根因根治（第五轮）**：生产复测遥测零上报 + 自检通过 → 排除播放侧。
    真实 API 复现实验：**每个 streaming_call 边界烙入 ~0.9s 静默**（整段喂 0 处；
    20 字块喂 5 处/34s ≈ 用户感知的 4-5 处；整句喂 3 处——减喂入次数治标不治本）。
    修复 `_PauseCompressor`：PCM 流逐 20ms 窗判决，每段静默保留前 0.35s、超出丢弃
    （零前瞻零延迟）。真实 API 验证同文本 5 处 → 0 处。`_audit_silence` 每轮收尾
    后台审计重播 PCM 作回归护栏（≥0.6s 静默 WARNING 带位置）。
19. **断句连贯性**：0.35s 残留边界微停顿仍可闻（用户复测 2 处）→ 喂入策略改句边界
    批量喂（`_take_first_unit`/`_take_feed_unit`：首播句末优先、≥8 字逗号兜底；
    后续 ≥60 字完整句、只切句末、断粮 2.5s 守卫），200 字回答喂入 12→4 次，边界
    全落自然停顿处；压缩机降为兜底。`TTS_ACCUM_CHARS` 默认 20→60，
    `TTS_FIRST_FRAGMENT_CHARS` 弃用。
20. **数字区间“-”不念**：`_convert_dash_ranges`——数字间 -/–/— → “到”，
    ISO 日期 → 中文日期（2026-08-12 → 2026年8月12日），非数字连字符不动。

### 根因与修复（按影响排序）

1. **首播慢**：分段独立合成等整段完成（~2s/段）+ 攒批门（5 chunk+2s）。实测流式首块
   ~0.6s 与文本长度无关 → **每回答单个流式会话**（`CosyVoiceTTS.start_stream`，PCM 格式），
   首段 8 字即喂，爬坡批次 0.4/0.6/0.8s→0.9s 边产边播。
2. **`streaming_complete()` 阻塞**（中途停顿真凶）：等全部合成完成（实测 26s+）且完成后
   close 连接，同步调用冻结发布（E2E 实证 playlist 冻结 22s）→ 后台线程执行（`respond`）。
3. **音频收集耦联 LLM yield**：LLM 流停顿（高峰实测 40s+）即断流 → answer_question 改后台
   泵线程，音频按 0.1s 节拍独立发布。
4. **前端 ke() 每批重建 hls**（我首轮 patch 引入的回归）：前端 effect 在每个音频批 yield
   都重跑 ke()，无条件 destroy 重建 MediaSource（缓冲清空，E2E 实证每 ~0.8s 一次）→
   **按 URL 去重**：同流复用、新轮才重建；原生 HLS 分支同理（该分支此前从未修复，
   Safari/无 MSE 浏览器第 2 轮必无声）。
5. **`lowLatencyMode:true`**：播放器贴 live edge，前向缓冲恒 ~0.1s（实证）→ 关闭；
   `maxBufferLength:1`→60s。
6. **gradio `MediaStream.max_duration` 每段 +1 蠕变**：TARGETDURATION 随段数膨胀，hls.js
   无更新时按 TD/2 轮询 → 停顿放大器（仿真 22.5s）→ 修正 clamp(ceil(段时长),1,5)，
   ≤1s 批次下 TD=1（规范内），轮询与缓冲解耦。
7. **转码吞吐**：pydub 双进程 0.7s/批（Windows）→ 单 ffmpeg 进程 stdin→stdout 0.23s/批；
   **EXTINF 改报真实解码时长**（AAC priming 2s→2.048s，声明漂移 48ms/段致 MSE 空洞、
   播放器卡固定位置，实证）。
8. **第 2 轮无声残留因素**：patch 原地改 JS、哈希文件名不变 → 浏览器启发式缓存长期跑旧
   文件 → `/assets/*.js` no-cache 中间件强制 revalidate。
9. **bug-123（顺带）**：answer_question 错误路径给 gr.JSON 喂空串 → postprocess 抛 Error、
   事件静默失败（用户看不到任何提示）→ 改 gr.update()/"[]"（4 个固化测试）。

### 新增/变更测试

- `tests/test_tts_broadcast.py`（27 个，全离线）：流式会话（mock SDK）、_PcmBatcher 爬坡、
  看门狗重建、respond 集成（首播中途产出/顺序保序/异常不炸）、前端 patch 标记、no-cache
  中间件、TD 修正、转码与 ADTS 时长、停顿仿真（shallow/growing 基线复现 + fixed 验证）。
- `tests/test_voice_ui.py`：3 个 respond 集成测试改流式假会话契约；删 2 个 `_maybe_play_batch`
  测试（函数随改造移除）；`test_respond_first_play_at_5_llm_chunks` 废弃（语义已变）。
- `tests/test_review_findings.py`：+4 个 bug-123 JSON 安全测试。
- **全量：533 passed, 0 failed**（基线 505）。

### 诊断工具（scripts/，留档）

`measure_tts_latency.py`（分段延迟）、`measure_tts_firstchunk.py`（首块延迟 ~0.6s 实证）、
`measure_tts_feedpattern.py`（喂法速率对比）、`tts_stall_sim.py`（停顿仿真器）、
`repro_hls_rounds.py`（多轮 playlist 服务端验证）、`e2e_tts_browser.py`（真实浏览器验收，
支持 --mock-llm 隔离 LLM 波动 / --one-round）。

### 残余风险与监控

- LLM 流中段长停顿（内容缺口）下播报必然中断后自动恢复：客户端缓冲（p50 12~30s）可吸收
  ~20s 级波动；更大停顿属上游供给问题，任何 TTS 工程无法弥补。
- dashscope 会话断流：看门狗 `TTS_STREAM_WATCHDOG_SECONDS`(15s) 重建（≤2 次，重喂最后片段，
  有界重复 ≤1 段）；`TTS 音频块间隔异常` 告警日志可观测 API 侧 >3s 断流。
- 首句 ≤1s 的物理下限说明：API 首块 0.6s + 转码 0.23s + 客户端启动 ~0.4s ≈ 1.1s 起，
  当前实测 1.2~1.5s；若必须 <1s 需换更低首块延迟的 TTS 通道或预连接会话池（SDK 不支持
  无文本预连接）。
- gradio 升级风险：4 个 monkeypatch/JS patch 均带结构校验，版本不匹配时跳过并告警
  （启动日志可见），测试 `TestFrontendPatchExtended`/`TestMediaStreamTargetDuration`/
  `TestAudioTranscodePatch` 可验证。


---

## 十、第十四轮：语音助手（audit-ASR，2026-08-12）

**需求（用户下达，6 项）**：自动唤醒（唤醒词可编辑，初版"你好小虎"）；前置 silero VAD
（0.5/400ms/800ms/200ms/15s 五参数）；双计时提问（播报后 8s 窗，段后循环延长 2s，
静默即自动提交）；多轮提问 + 随时打断播报；说完到首字 <1s；纠词典支持
`[{"from","to"}]` 列表格式。设计/计划：`docs/superpowers/specs|plans/2026-08-12-voice-assist*`。

### 关键实证（非推测）

1. **silero-vad pip 包不可直接用**：`silero_vad/__init__` 顶层 import torchaudio
   （服务器 conda 无此包，本地 DLL 亦损坏）→ 自持 ONNX 推理（onnxruntime 直调），
   模型用仓库内置 `src/assets/silero_vad.onnx`（v6 16k 专用导出，1.3MB，与全量版
   逐窗概率实测一致）。**官方推理每窗须前拼上窗末 64 采样作上下文**（utils_vad 源码
   实证），缺失则概率输出全废（实测真实语音峰值仅 0.13，修复后 1.0）。
2. **gradio 6.22 事件并发=1**：自动提交若塞进 stream 事件，respond 30s+ 运行期间
   音频块全排队、打断检测失效 → 隐藏 Textbox `.change` 独立事件承载自动提交/欢迎语。
3. **gradio 录音无 AEC**：`getUserMedia({audio:true})`（record.esm 源码实证）——
   一体机外放下 TTS 播报被自身麦克风拾取，VAD 必误判 → `patch_gradio_mic_aec()`
   强制 echoCancellation 三件套（浏览器 AEC 以本页输出为参考），叠加"播报期只 VAD
   不送 ASR"双保险；`verify_frontend_patches()` 自检扩为 3+1 标记。
4. **打断必须前端强停**：客户端 HLS 缓冲最深 60s，服务端停发不够 → head JS
   MutationObserver 观察 voice_status 的 ⚡ 标记暂停 `<video>`；服务端按 session_hash
   定位 token，respond 主循环/排空双检 cancel（停喂停发、取消会话、跳过重播写入）。
   **cancel 中的 token 按非激活上报**——否则打断后 respond 收尾的 ~0.1s 窗口内状态机
   被抖回 broadcast 态、吞掉新问题（测试固化）。
5. **提速结论**：保持讯飞流式（wpgs 边说边出字，实测增量 <500ms），"说完到首字 <1s"
   天然满足（说完时首字早已在屏）；否决一次性重识别（必超 1s）。

### 真实 API 全链路冒烟（scripts/smoke_voice_assist.py，非 pytest）

「你好，小虎」夹具 → greet 命中；模拟播报收尾 → 进 listen（8s 窗）；「请介绍一下
司母戊鼎…」夹具 → wpgs 部分结果逐块上屏（含动态修正：私募屋→司母戊鼎）→ 段结束
2s 静默 → 自动提交「请介绍一下司母戊鼎的历史背景和文化价值？」。**全链路通过**。

### 架构与变更

- `src/vad.py`：StreamVAD（五参数分段状态机，语义随 silero VADIterator：段长 <min_speech
  丢弃、≥min_speech 提前发 confirmed_start、max_speech 强制切段、pad 前后补偿、段间
  reset LSTM 状态）；SileroVadOnnx（自持推理）；create_vad（抛可操作原因）/try_create_vad
  （→None 降级手动模式）。
- `src/voice_assistant.py`：VoiceAssistant 四态（standby/await_broadcast/broadcast/listen），
  纯逻辑零 gradio 依赖（假 VAD/假 ASR/假时钟全离线测试）；唤醒匹配先归一（去标点）
  后纠错（ASR 标点会切断错词致纠错失配，实证："泥好，小胡！"）。
- `app.py`：voice_stream_dispatch（恒 5 元组，手动模式零变化）；_BroadcastToken 注册表；
  respond 取消分支；auto_respond/play_greeting（欢迎语内存+磁盘缓存，零合成延迟）；
  UI elem_id + 隐藏触发组件；`_voice_assist_head()`（自动点录音 + 打断强停，仅助手模式注入）；
  `_voice_assist_startup_probe()`（启动自检：assist 开启先验 VAD，失败 ERROR 日志）。
- `src/asr.py` load_dict：顶层列表=纠词典；dict 形态新增可选 wake_words/wake_greeting
  （项目文件定义即整体替换全局）。
- 配置（`.env`）：VOICE_ASSIST_ENABLED（默认 false——手动模式行为零变化）/
  ASR_WAKE_WORDS/ASR_WAKE_GREETING/ASR_INITIAL_WAIT_S=8/ASR_EXTEND_WAIT_S=2/
  VAD_THRESHOLD/VAD_MIN_SPEECH_MS/VAD_MIN_SILENCE_MS/VAD_SPEECH_PAD_MS/VAD_MAX_SPEECH_S/
  SILERO_VAD_MODEL_PATH。
- 依赖：+onnxruntime（纯 CPU）；**不要** pip install silero-vad（torchaudio 重依赖）。

### 测试

`tests/test_voice_assist.py` 55 项（全离线）：VAD 五参数逐项（脚本化假模型）+ 真实模型
对 TTS 预生成夹具/静音/噪声冒烟 + FSM 全迁移（唤醒/双计时/循环延长/打断/超时回落）+
app 接线（动作翻译/注册表/自动提交 nonce/欢迎语缓存/respond 打断）+ 前端 patch 标记 +
VAD 失败诊断（原因上屏/启动自检）。
**全量：618 passed, 0 failed**（基线 563）。

### 残余风险与监控

- AEC 残余回串致误打断：400ms 最短语音过滤 + 播报期不送 ASR；生产日志观测
  `语音助手状态: ⚡` 频率，误判多则上调 VAD_THRESHOLD。
- 待机态每个语音段烧一次讯飞 IAT（VAD 门控后量小）；终局迁移前端 sherpa-onnx KWS
  （落地方案 §5.5，隐私 + 零额度）。
- 自动点录音被浏览器策略拒绝：head JS 重试 20s + 控制台告警，用户手动点录音兜底。
- gradio 升级：麦克风补丁带结构校验，未匹配跳过并告警（自检 ERROR 日志可见）。
- VAD 初始化失败的部署侧误诊（用户复测实证：UI 只让"详见日志"，运维不知道修什么）
  → 修复：失败原因直接上屏（缺 onnxruntime / 缺模型文件一眼可辨）+ 启动自检
  `_voice_assist_startup_probe()`（assist 开启时先建 VAD 会话验证，失败即 ERROR 日志）。

### 修复轮2（用户复测三问题，全部实证定位）

1. **"说出唤醒词后走了 LLM"**：唤醒匹配原本只在待机态；用户在倾听态（8s 窗）说唤醒词
   被当问题提交。修复：倾听态整句命中唤醒词→重新应答；唤醒词前缀自动剥离
   （"你好小虎，司母戊鼎…"→问题"司母戊鼎…"）。
2. **"不知道什么状态"**：状态提示原本只在切换瞬间闪现 → 常驻状态行（每块重算、
   有变化才上屏）：待机中（唤醒词提示）/倾听中（剩余秒数）/播报中（可打断）等；
   欢迎语全文经状态行展示（初版写对话框，与 respond 末趟在途更新互写丢失，E2E 实证
   后移出——chatbot 共享可变状态跨事件写必然竞争）。
3. **对话框乱码**：`[['add','[value]','问题\u200b#2']]` —— 隐藏 Textbox 组件值被
   gradio 6.22 流式 diff 协议串线（更新指令当成值）。根修：文本一律走服务端
   pending 存储（`_pending_questions`/`_pending_greet`，消费一次性），触发器改
   gr.State（服务端值跟踪 + deep_hash 变更检测，前端不可达→免疫）。E2E 实证零乱码。
4. **gradio 6.22 流式收尾 KeyError（新发现的 gradio 侧 bug）**：生成器事件末趟
   final pass 输出全 None，流式输出若从未开流（TTS 关闭/被打断跳过收尾）则
   `stream_run[output_id].end_stream()` KeyError → 事件收尾中断、末批输出丢失。
   `patch_gradio_stream_endstream_guard()`：末趟预检降级为空 update。
5. **onnxruntime DLL 初始化失败（用户"VAD 初始化失败"根因）**：裸进程各种顺序均
   正常，但服务器进程工作线程里 lazy import 4/4 必现 → app import 期主线程预加载。
6. **自动点录音**：①hydration 竞争——过早点击落在未就绪按钮上，UI 显示录音中但
   零流事件；②判据三连坑——UI 录音态假阳性、WebSocket 挂钩（6.22 流块不走 WS）、
   fetch 挂钩（不走逐块 POST）均不可观测 → 最终判据：voice_status 出现服务端文本
   （排除"录音已停止"假阳性），6s 无流通则停止重录自愈；③playwright 同步 API 只在
   调用时泵事件循环，sleep 空转收不到 console 事件（E2E 脚本层教训）。

E2E：`scripts/e2e_assist_loop.py`（全链路：自动录音→自动提交干净气泡→唤醒应答）
+ `scripts/e2e_autorecord.py`（自动录音+流通确认）。**全量 628 passed**。

### 优化轮3（用户复测提速：唤醒应答 2-3s / 转写慢）

**延迟构成（先分解后优化）**：唤醒→应答 2-3s = VAD 端点 800ms + ASR finish ~0.3s +
块节奏 0.5s + HLS 编码发布 ~0.2s + 客户端起播 ~0.4s。

**优化（真实 API 实测，`scripts/measure_asr_latency.py`）**：
1. **唤醒词提前命中**：待机态在 wpgs 部分结果上匹配唤醒词（词尾后 ~0.3s 可见），
   不等 VAD 静音端点 → 实测词尾→greet 动作 **0.15s**（旧路径 ≥1.5s）。
2. **应答音频预置直播**：固定应答语合成一次（启动后台预热+内存/磁盘缓存），
   经 `GET /__voice_greeting` 由前端 `new Audio()` 预加载直播（~0.1s 起播），
   绕开 HLS 链路；服务端 play_greeting 仅 token 等待驱动状态机（可打断）。
3. **转写提速**：VAD 段端点 800→500ms（关键论证：端点激进不会切碎问题——
   2s 延长计时会把分段续接成同一问题）+ 流块节奏 0.5→0.3s。
   实测：说完→定稿上屏 **~0.7-0.8s**；说完→自动提交 2.5s（其中 2s 为需求3
   既定延长计时参数，可调 `ASR_EXTEND_WAIT_S`）。

**全量 637 passed**；E2E 全链路复跑通过（含 /__voice_greeting 调用断言）。


---

## 附：当前 git 基线快照（2026-08-12 深夜，语音助手收官）

- `origin/main` = **c043769**（feature/asr 已 --no-ff 并入；含 T1-T7 + 修复轮1-2 + 优化轮3）
- `origin/feature/asr` = 3461019；`origin/feature/audio` = c3fc7ac（冻结，不再动）
- 远程：origin = 本地 Gitea（localhost:3000）；github 远程受网络限制未同步
- 全量测试：**637 passed, 0 failed**（全离线）；真实 API 冒烟/E2E 脚本见 scripts/
- 下一阶段：竖屏一体机前端开发（依据《数字人一体机落地方案》），内核零改动只加薄层

---

## 十一、第十五轮：数字人前端与服务端薄层（feat(web)，2026-08-14~16）

**范围**：全新数字人前端（竖屏一体机/大屏，Vue3+Vite+TS+three 0.154）+ 服务端薄层
`kiosk_server/`（FastAPI，:7861，全部新增文件；`src/`、`app.py` 冻结零改动）。
设计/计划：`docs/superpowers/specs|plans/2026-08-14-*`；部署：`deploy/README.md`。

### 架构事实（以此为准）

- 单通道 `WS /ws/voice`：PCM 上行（16k）→ VAD/FSM/ASR → query_stream → CosyVoice 单会话流式
  → PCM s16le 24k 直推 → 前端 WebAudio 链式排播（弃 HLS/AAC——首播 0.62s 实测，打断=前端清队列）。
- REST：`/api/health|config|presets|ocr`；token 可选（WS 走 query 参数，浏览器 WS 无自定义头）。
- 重播为端侧 PCM 缓存（参考工程既有模式，零网络往返）；多轮历史 4 轮挂 WS 会话。
- persona=湘小图（`.env ASR_WAKE_WORDS/ASR_WAKE_GREETING` 覆盖，零代码）；纠词典补
  `像小图/像小徒/乡小徒/像小偷→湘小图`（实测讯飞把合成「你好湘小图」识别为「你好像小图」）。

### 本轮自发现并已修复（测试固化）

1. **薄层收尾期打断后 audio_end 误发**（else 分支落空缺陷）→ 打断不再发 audio_end/留存重播（web-007）。
2. **Vue reactive 数组元素持有原对象导致视图不更新**（聊天流式文字不刷新）→ 回取代 agent 持有（web-021）。
3. **测试未隔离本地 .env**（部署配置唤醒词致 637 基线红）→ `Settings(_env_file=None)`，断言意图不变（web-005）。
4. simple-keyboard 首挂不渲染（watch 非 immediate）；测试假件跨实例共享监听器；`VoiceWsClient.connect` 重入风险（幂等守卫）。

### 实证记录（真实 API/真实浏览器）

- 服务端全链：FAQ 路径首文本 0.05s/首音频 0.62s；检索路径 1.79s/2.37s；语音全链
  唤醒@2.1s→应答→wpgs 上屏→2s 静默自动提交→回答+播报（`scripts/smoke_kiosk_ws|voice.py`）。
- 前端 E2E：预设点击→首字 @3.2s→定稿→MusicBar 挂载（`scripts/e2e_frontend_chat.py`）；
  免提自动开麦+推流+FSM 待机活跃（`scripts/e2e_frontend_voice.py`）。
- 手写 OCR 真实识别：qwen-vl-ocr-latest 正确识别「你好」测试图。

### 残余风险与监控

- **Chrome fake-file 音频注入在当前 Chromium 无效**（mic RMS 静音底实证）——浏览器内容级语音
  E2E 暂以服务端冒烟为准，现场验收清单（deploy/README.md §4）兜底。
- **Qdrant 文件锁互斥**：kiosk_server 与 Gradio 不能同时在线（运维手册已写）；远程模式可选增强。
- FSM 个别状态文案硬编码「小虎」（冻结内核词面问题，功能无影响）。
- AEC 在真实麦克风阵列+外放一体机上的残余回串待现场验证（双保险已就位：浏览器 AEC + 播报期不送 ASR）。
- PCM 带宽 48KB/s/路（局域网无感；公网多机并发需再评估，协议 format 字段可演进 AAC）。
- 免提常开收音的浏览器进程长期稳定性（720h 无人值守）待现场 soak；已有 300s 空闲自刷新兜底。
- **测试**：pytest **692 passed**（637 基线 + 55 薄层新增，全离线）；前端 vitest **47 passed**（独立计数）。

## 十二、第十六轮：PC 验收体验反馈修复（web-029~038，2026-08-17）

PC 端真机验收（语音/预设/键盘/手写/打断/长回答全链）后的反馈修复轮。原则不变：
src/ 冻结内核零改动，一切修正在薄层与前端；修复均带离线测试与真实链路证据。

### 根因→修复→证据

| # | 现象 | 根因（实证） | 修复 | 证据 |
|---|---|---|---|---|
| web-029 | 连续问答新提问被丢弃 | WS 层 busy 时回 error 丢帧 | `BroadcastSession.ask` 内串行化（barge+有界等收尾），事件严格不乱序 | 会话/WS 两层打断用例 |
| web-030 | 首播 2.9s 偏慢 | 首句等待标点才喂 TTS | 首播硬地板 12 字硬切（括号平衡护栏） | 首音频 2.06~2.91s（真 API） |
| web-031 | 界面金色舞台≠设计稿、碎图 | 误用参考 v2 切图；箭头图不存在 | 全套切 v1 森林主题；分隔线改纯 CSS 叶饰；字体加载实证 | 1080×1920 截图比对设计稿 |
| web-032/033 | 双击预览 bat 没反应 | **LF 行尾致 `^` 续行失效** | CRLF 重写+单行命令+Edge 兜底 | remote-debugging 实证开窗 |
| web-034 | 非 9:16 窗口布局变形 | vh 体系只保纵向比例 | 1080×1920 设计坐标+舞台等比缩放 letterbox；弃 px-to-viewport | 540×960/917×1009/1080×1920 三档截图 |
| web-035 | 开页即显示"正在录入语音" | 麦开着≠检测到声音，映射错误 | `speaking` 标志（partial 置位/answer_start·聆听复位）；初始=「请说"你好，湘小图"唤醒」；删待机状态行 | 胶囊四态单测+截图 |
| web-036 | 知识库外问题拒答 | 内核降级路径中事实类 `_should_enable_search`=False（总开关开也不联网） | 薄层 `web_fallback.py` 出口拦截拒答模板→百炼 enable_search 流式作答；失败回退原话术；开关可关 | 图书馆简介/家博会/天气三路径真 API 冒烟 |
| web-037 | 头像失真、状态行压线带图标 | 强压 123×123 圆裁（avatar_me 295×157）；FSM 文本自带 emoji | 头像 height:auto 自然比例；客户端正则剥图标+上移+加大 | 渲染盒 115×61.2=原图比例 |
| web-038 | 长回答溢出屏幕；返回钮遮气泡；播放钮出框 | panel-inner 缺 border-box；聊天区 height:100% 超剩余空间；MusicBar min-width 384 超气泡宽 | 固定窗口+flex/min-height:0 内部滚动；返回钮独立头行；MusicBar 宽度随气泡收缩 | 容器底=面板底；2530px 合成内容可滚；播放钮内缩 35px |

### 结论与残余风险

- 全部反馈闭环；pytest **705 passed**（+8，全离线）；前端 vitest **48 passed**（+1）。
- web-036 兜底代价：知识库外事实类问题多一次检索耗时（首音频 ~3.9s vs KB 内 ~2s）——检索层属冻结内核，物理下限如此。
- 联网兜底答案质量依赖百炼搜索；提示词已约束简洁口语化/不编造，现场可按需调 `FALLBACK_SYSTEM_PROMPT`。
- 残余监控项同 §11（AEC 阵列实测、720h soak、Qdrant 互斥、PCM 公网带宽）。

### 增补：web-039 主题点缀动作（用户确认需求，2026-08-17）

- 需求：数字人根据回答内容做相应动作；无匹配 → 随机组合播放；验收标准=**衔接自然无停顿感**。
- 方案（纯前端，零延迟/零后端改动）：`THEME_RULES` 高置信规则表（q/a 分域 + deny 否定过滤）；
  greet 固定挥手；首 chunk 本地匹配（微秒级）发一次 onAction；未命中维持 TALK 随机池。
- 衔接保障：`playAccent` 单次播放 + 双向 0.4s crossFade + **提前 0.4s 起回切**（尾帧夹持与
  池动作淡入重叠）；play() 恢复 LoopRepeat 防 LoopOnce 残留。
- 证据：映射/分域/否定/轮次单测 6 项；真机探针实测 `playAccent('shuangshoubixin')` 点火
  + 峰值/回切帧截图；26 帧连续抓帧 **0 冻结**；vitest 54 passed；pytest 705 passed 无回归。
- 已知边界：语义覆盖受 13 剪辑资产上限（后续加剪辑=纯配置扩展）；关键词误判率由 deny 表+
  保守主题收敛控制；非口型动作仅作 2~4s 点缀后回说话池（口型动作才适合全程）。

### 增补：web-040 慢流播报死亡修复（用户反馈，2026-08-18）

- 现象：「有什么电影推荐？」~1 分钟出答案，语音只播前几个字后全程无声。
- 根因（服务端日志实证）：联网搜索长流 chunk 间隙 >15s → `_broken()` 误判 TTS 卡死
  （fed 非空 + 未完成 + 15s 无音频）→ 重建(1/2)(2/2) → dead 放弃整轮播报。
  误诊本质：把「LLM 流式间隙静默」当「TTS 无音频超时」——间隙期间 TTS 音频早已追上喂入。
- 修复（薄层 chat.py，内核零改动）：`_broken()` 增加积压判据——仅在「最新一次喂入后
  无音频返回」（last_audio_at < last_feed_at）时才允许看门狗触发；喂入时刻提前到 _feed 前记录。
  复测：同一问题 992 字回答 158s 音频全程流完、0 次看门狗事件；回归测试旧逻辑复现
  handles==3、修复后 ==1；原「真卡死→重建≤2→放弃」测试保持不变（mute 场景仍触发）。
- 附带兜底加固（web_fallback）：max_tokens 硬限 320、历史裁 1 轮防内核人设渗透、剥 `**`。
- 残余说明：重排冷缓存 ~40s（DashScope rerank API）与搜索生成耗时属冻结内核/外部 API；
  意图分类随机性导致该问题可能走内核闲聊联网路径（长度/人设属内核所有），薄层不越权。
- 测试：pytest 707 passed（+2）。

### 增补：web-041 回答限长 320 tokens（用户拍板，2026-08-18）

- 背景：`.env LLM_MAX_TOKENS=4096` → 联网/闲聊路径答案冗长（实测 992 字）。
- 方案：薄层 `services.apply_kiosk_llm_caps()` 在生产入口（任何 pipeline 加载前）进程级
  钳制 `settings.llm_max_tokens→320`（幂等、只降不升）；.env 与内核零改动，
  **Gradio 进程不受影响**（控制台长回答对调试有价值）。
- 证据：同问题 992→431 字、102.7→48.3s、音频 158→64.6s；KB 路径 80 字回归不变；
  单测覆盖钳制/幂等/低值不动；pytest 709 passed（+2）。

### 增补：web-042 聊天态体验三修（用户反馈，2026-08-18）

- 返回钮不明显 → 80→104px + drop-shadow 与羊皮纸底色分离。
- 答案框下框顶到屏幕边缘（流式中段长文硬切）→ 滚动视口 bottom 上移 84px 让出状态行区
  + 底部 44px 渐隐遮罩；实测滚动区底 1836 < 状态行顶 1855 < 屏幕底 1920（1080×1920 舞台）。
- 语音提问停留在首页（问答气泡不可见）→ `useAutoChat` 组合件：await_broadcast/broadcast
  变化沿切 chat；listen 不跳、手动返回不回弹。单测覆盖三沿 + 真实页面沿注入实证跳转。
- vitest 55 passed（+1）。

### 增补：web-043/044 问答内容优化轮（用户拍板范围，2026-08-18）

- **范围确认**：本轮只做「口语化/播报友好度」（web-043）+ 新增「本地大模型双通道」
  （web-044）。记录在案不实施：基线集/人设修复路径/Markdown 清洗（位置已定=前端展示层）/
  预设池扩充/rerank 预热；内核 chitchat·RAG 提示词（小虎/家博会）维持冻结。
- **web-043 兜底提示词强化**：`FALLBACK_SYSTEM_PROMPT` 增补——连贯单段、禁列表/编号/
  项目符号、避英文术语（必须用时中文说法）、短句适合朗读；既有约束保留。测试 3 项。
- **web-044 本地大模型双通道（百炼/本地并存可切换）**：
  - 配置（仅新增，默认零变化）：`LLM_PROVIDER=dashscope|local` + `LOCAL_LLM_BASE_URL/
    API_KEY/MODEL/CONTEXT_TOKENS`；密钥仅服务端（.env gitignore，config 默认空）。
  - 内核 `src/llm.py`：`LocalOpenAILLM`（接口/行为与 BailianLLM 对齐：日期注入、逐 token
    去 emoji、退避重试、已 yield 不重试、4xx→FatalAPIError、llm_cache 隔离 key）+
    `create_llm` 工厂；`BailianLLM` 字节级不变；`RAGPipeline` 改经工厂取 LLM（单点，
    其余内核零改动）→ provider 切换即 RAG/闲聊/意图 L2 全路径生效；Gradio 同享。
  - 薄层兜底同步：provider=local 时 `_local_answer_stream`（湘小图人设/320 硬限/裁 1 轮
    历史/出口 emoji+** 双清洗）；dashscope 路径字节级不变。
  - 本地能力边界（如实说明）：无私有联网能力——`enable_search` 仅告警忽略、不追加搜索
    引导（兜底路径=模型自有知识作答）；embedding/rerank/意图 L1 仍走百炼（需 DashScope
    key 在线）；本地上下文 4096 → completion 按预算自动钳制（实测 169+4096 被 vLLM 400
    拒绝后修复）；`openai>=1.55,<2`（httpx 0.28 兼容，旧版 1.51 实测 proxies= 报错）。
  - 证据：真实冒烟 `scripts/smoke_local_llm.py`——local：工厂→LocalOpenAILLM、
    chat 0.60s、流式首字 0.10s/全程 0.57s、兜底 0.94s 无清洗残留；dashscope 回归：
    BailianLLM、chat 1.49s、首字 0.56s。离线测试 29 项全绿。
- **测试**：pytest **738 passed**（+29：config/工厂/chat/stream/重试/4xx/钳制/管线接线/
  兜底分支）；前端 vitest **55 passed** 无回归。
- **配套文档/配置（web-044 收尾）**：`deploy/OPERATIONS.md` 运维手册（三端启停 + 双通道
  切换标准三步 + SSH 隧道联调 + 故障速查）；`.env.development→127.0.0.1:7862`
  （实测定位：本机与服务器唯一通道是 SSH 隧道——防火墙只放 22 且 ub-server 主机名
  本机不解析，preview 无预设/无响应即此因）；单元模板按 ub-server 实录修正。
  服务器部署实证：百炼通道全链 OK（首文本 3.88s/首音频 4.68s）、local 通道冒烟 OK。

### 增补：web-045 本地模式 KB 问题空气泡修复（用户反馈，2026-08-18）

- **现象**：local 模式下 KB 相关问题（"我要买沙发，要怎么推荐？"）无答案、空气泡；
  闲聊/兜底路径正常。
- **根因（实证）**：KB 路径 prompt = 指令 + 检索上下文（内核 MAX_CONTEXT_CHARS=30000）
  + 历史 → 超出 vLLM 4096 窗口；web-044 钳制只限 completion → prompt 本身 6969 tokens
  被 400 拒绝 → FatalAPIError → chat.py 发 error 事件（无文本）→ 前端空气泡。
- **修复**：`LocalOpenAILLM._fit_messages_to_window`（自家类，零冻结区改动）——
  预算=窗口−32−min(max_tokens,256)；先丢最老历史（保 system+当前问题，连续前缀
  丢弃不破角色交替），仍超截 system 尾部（保头部指令+"……（参考信息过长已截断）"标记）。
- **证据**：复现脚本同 prompt 由 FatalAPIError → 流式完整作答；副本 KB 真实管线
  （绕开本机 Qdrant 锁）两路径实测：relevance-fail 优雅拒答无崩溃、KB 命中 96 chunk
  流式作答（est_prompt=2661）；离线 +4（历史丢弃/截断标记/流式适配/预算）。
- **观察（如实说明，非本 bug）**：local 模型做相关性确认与 qwen-plus 判定存在差异
  （沙发题被判不相关→拒答→薄层兜底作答）——换模型的固有行为差，兜底保证有答案；
  相关性阈值的跨模型校准属冻结内核，未动。
- **测试**：pytest **742 passed**（+4）；vitest **55 passed** 无回归。

### 增补：web-046 Markdown 残留完整修复（用户反馈截图 22，2026-08-18）

- **现象**：答案上屏满屏 `**`/列表符/「- 」，且影响播报（未配对标记、序号被读出，
  个别符号致播报失败）。内核 RECOMMEND 提示词主动要求结构化 `**` 输出（冻结区），
  故清洗修复（既定决策：展示层在前端）。
- **展示侧（前端）**：新增 `src/utils/textClean.ts cleanForDisplay`——渲染时清洗
  （原文/重播缓存不动，天然处理跨 chunk 未配对 **）；剥粗斜体/标题/引用/列表符/
  链接/图片/表格/删除线/行内代码/HTML；**硬约束不误伤**：小数（2.2号馆）、日期区间
  （3月18日—21日）、3~5、百分比、货币、列表序号（展示保留）、乘法式（3*5）；
  剥离后压缩空白保连续性。ChatPanel 鹿气泡接线（用户气泡不动）。
- **播报侧（薄层）**：新增 `kiosk_server/tts_clean.py clean_for_broadcast`——内核
  clean_text_for_tts 之后保守补充：未配对 `**`/贴中文单 `*`、有序列表前缀
  「1. 」「2、」（`(?!\d)` 护小数）、条目分隔「 - 」→逗号；仅剩标点判空（调用方跳过）。
  chat.py 两处喂入点接线（流式分段 + 收尾段）。
- **证据**：真实截图文本 before/after（展示/播报双侧实证：`**` 全剥、2.2号馆/序号/
  标点完好、连续成段）；真实管线净答案幂等无损伤；npm run build 通过。
- **测试**：vitest 73 passed（+18：剥离 7 类 + 保护 11 例）；pytest 763 passed（+21）。

### 增补：web-047 返回中断播报（用户反馈，2026-08-18）

- 现象：聊天态点「返回」回首页后语音播报仍在继续。
- 根因：ChatPanel.onBack 只 resetChat + emit，未停播未取消（实证读码）。
- 修复：onBack 先 `player.stop()` 本地立即静音（不等服务端回包），再 `barge()`
  通知服务端取消本轮生成/播报。单测覆盖「播报中点返回→本地停播+服务端取消」。
- 测试：vitest 74 passed（+1）。

### 增补：web-048 唤醒应答提速（用户拍板方案 2，2026-08-18）

- 现象：唤醒成功但默认应答 2~3s 才播（用户要求 ~1s）。
- 定位（实测分解）：应答语确有内存+磁盘缓存+启动预热（两轮延迟完全一致，无合成耗时）；
  慢在唤醒判定——待机部分结果提前命中通道的评估窗口仅「in_speech 期间」，
  而讯飞完整部分结果多数在说话结束后、VAD 端点闭合前的窗口内才到齐 → 必落
  「端点 500ms + finish 往返」慢通道（服务器实测稳定 +1.22s）。
- 修复（内核 1 处条件，用户逐项批准；其他内核模块零影响）：`voice_assistant.py`
  部分结果评估窗口放宽为「ASR 会话存活期间」。匹配规则不变，不产生新误唤醒类别。
- 证据（真实讯飞 + 真 VAD + 真缓存应答，本地 7863 实例四轮连测）：
  Δ流尾→应答开播 = 0.27/0.24/0.25s（修复前 1.22s 级）；1 轮因讯飞部分结果迟到
  超出端点窗仍走慢通道 1.24s（外部 API 抖动，如实说明）。
- 测试：新增 2 项（端点窗迟到 partial 提前命中 + 非唤醒词不误唤醒）；
  pytest 765 passed；vitest 74 passed；npm run build 通过。

### 增补：web-049 答案触上限硬截断修剪（用户反馈截图 12，dashscope 模式，2026-08-20）

- 现象：长答案被 max_tokens=320（web-041 钳制）硬截断，气泡以半个短句戛然而止。
- 修复（薄层 chat.py，内核零改）：`_looks_truncated`（长度≥max_tokens×1.2 字水位 +
  结尾无句末终结符，双条件防误判）→ `_trim_to_last_sentence` 裁至最后完整句——
  三处一致：上屏（answer_end.full_text 修剪版，前端本就在 answer_end 替换全文）、
  播报（不喂悬尾段）、多轮历史（记修剪版）。打断轮不修剪（残段是用户主动行为）；
  长而有完整结尾/短残段/无句边界情形均原样不动。
- 证据（真实 DashScope，模拟 kiosk 320 钳制，用户同场景电影题）：原始流 424 字
  断于「…是亲子与怀旧影迷的」→ 上屏 257 字止于「…引发热议。」完整句。
- 测试：pytest 771 passed（+6：截断三处一致/长句有尾不动/短残段不动/打断不动/
  判据边界/修剪函数）；vitest 74 passed 无回归。

### 增补：web-049 补强——截断轮播报与上屏句点对齐（用户反馈截图 13，2026-08-20）

- 现象：上屏已止于修剪线（完整句），但 TTS 播报提前结束（停在更早位置）——
  修剪线内的完整句没播完。
- 根因（自产 bug，实测定罪）：初版收尾在截断时整段跳过 buf，而 buf 里除悬尾碎片外
  还积压着不足 accum=60 字阈值、等收尾喂入的完整句 → 被一并丢弃。
- 修复：利用 `full == 已喂原文 + buf` 恒等式，收尾只喂 `trimmed` 中尚未喂入的尾部
  （`trimmed[len(full)-len(buf):]`）——丢的仅是悬尾碎片，完整句照播。
- 证据（真实 DashScope 电影题）：原始流 427 字断于「…烂番茄」→ 上屏 349 字止于
  「…沉浸式放松。」、播报喂入止于同一句点（判定一致）。
- 测试：pytest 772 passed（+1 回归：修剪线内完整句必播/悬尾不播/历史一致）；
  vitest 74 passed 无回归。

### 增补：web-050~063 AI 故事绘本（全新功能，brainstorming 全程拍板，2026-08-20）

- **范围确认**：用户对一体机说「给我讲一个〈任意主题〉的故事」→ 翻页式图文绘本 +
  逐页语音讲解。设计稿 D1~D10 全部经用户逐项拍板（`docs/superpowers/specs/
  2026-08-20-story-book-design.md`），实施计划 14 任务 TDD（`docs/superpowers/plans/
  2026-08-20-story-book.md`），subagent 驱动执行 + 逐任务评审。
- **决策溯源（D1~D10 拍板值）**：翻页式 8~10 页（一页=一图+≤80 字段文，儿童适宜）；
  分镜脚本 qwen-plus 固定云端（独立 1600 tokens 限长，先例 FALLBACK_MAX_TOKENS）；
  插图 qwen-image-3.0（严格遵循文字、跨图一致性、异步并发 ≤4、失败重试 1 次→占位）；
  绘本全流程固定云端不随 LLM_PROVIDER；薄层正则拦截意图（宁漏勿抢，无主题不触发，
  不做「再讲一个」专属状态）；播完自动翻页 + 随时手动翻页即切播报；讲完停留收尾页
  （空闲计时回首页）、中途返回=静音+取消+回首页、绘本全程无语音打断；同名故事缓存
  500MB LRU；脚本 60s/单图 90s/总预算 300s 超时；审核拦截→礼貌拒讲退出；插图服务端
  落盘 `data/story/`（前端不碰 OSS 临时链）；方案 A WS 单通道扩展。
- **关键实测（开工前 probe）**：`qwen-image-3.0` 必须走 MultiModalConversation messages
  格式（老 ImageSynthesis 400 InvalidParameter）；速度杠杆 `prompt_extend=True` 71.0s →
  `prompt_extend=False` 17.9s → 再 `size=1024*1024` 12.9s（prompt_extend=False 同时是
  「不过度发挥」的语义保证）；平台限额 RPM=20。
- **架构**：新模块 `kiosk_server/story.py`（意图正则/ScriptClient/ImageClient/StoryCache/
  StorySession）；播报复用专用 BroadcastSession 实例（StoryPagePipeline 喂当页文本，
  句边界/清洗/看门狗/打断串行化原样继承，answer_* 事件经包装改名 story_speak_*）；
  VoiceSession 故事态丢弃上行音频帧（唤醒/ASR/语音打断全静默）；WS 增
  story_page/story_finish/story_cancel 三消息；`GET /api/story/<id>/img/<n>` 供图
  （token 走查询参数）。前端 StoryBook.vue + useStorySession（页码主导、
  PcmPlayer.onEnded 播尽 + speak_end 双序护栏自动推进、乐观翻页、占位图淡入），
  HomeView home/chat/story 三态，预设池加故事引导。
- **实施期缺陷修正（评审闭环，均已回归）**：线程池超时 with 块陷阱（shutdown(wait=True)
  卡死使超时失效 → shutdown(wait=False)，Task 3/4 同款同步）；LLM 审核类 HTTP 400 被
  永久判死（重试循环外分类，story.py:283）；finish 打断在播页裁尾（先排空再播收尾语）；
  前端 drained 初值 true 致 speak_end 单独触发翻页截尾音（改 false + story_speak_start
  复位）；无 TTS 降级路径永卡第 1 页（speakStarted 兜底）；故事舞台 z 序盖小鹿防护。
- **证据（真实 API 冒烟，scripts/smoke_story.py「霸王别姬」，留档 data/story_smoke/）**：
  脚本 7.7s 出 10 分镜（每段 25~44 字全 ≤80，LLM 自主适龄化改编「霸王别姬」→
  「小霸王和小花姬」幼儿园京剧故事）；插图 3 张 7.2/12.6/7.9s 出图，目检达标（水彩
  绘本风、严格切题、角色锚定生效、无文字水印）；A/B 对比图留档。
- **测试**：pytest **829 passed**（+57：配置族/意图正则/脚本/插图/缓存/启动链路/播报
  状态机/VoiceSession 集成/WS+供图/预设/终审补强/验收反馈）；vitest **102 passed**（+28：
  WS 方法/useStorySession/StoryBook/Home 接线/终审补强/验收反馈）；npm run build 通过。
  外部 API 一律 mock，真实 API 仅冒烟脚本。

### 增补：web-063 终审与补强（SDD 全分支终审，2026-08-20）

- **终审**：14 任务全绿后派全分支终审（5b15c55..e48aadc，27 提交）——架构/协议/线程/
  测试真实性获肯定；发现 4 Important + 2 Minor，其中 F1/F3/F4 为 spec 拍板项落地不全。
- **补强（F1~F5，两笔提交，均先红后绿）**：
  - F1 LRU 接线：`evict_if_needed()` 此前实现并测试但无调用方（500MB 上限名存实亡）
    → 接入 `StorySession.start()` finally（全退出路径生效，失败仅记日志不拖垮拆解）。
  - F2 插图原子落盘：`_download` 直写目标路径，中断留截断残文件会被缓存命中路径误用
    → 同目录 `.part` + `os.replace` 原子改名，异常清理临时文件（3 项原子性测试）。
  - F3 返回钮 z 序：`.story-overlay` z20 盖 `.btn-back` z10，准备期/收尾页无法点返回
    （违 D7）→ btn-back z30（.storybook 根叠层上下文内决定性压制）。
  - F4 story_error 可见化：errorText 此前无渲染点且立即弹回首页（D8 拒讲话术静默）→
    preparing 盖层渲染拒讲话术 + 持留 2.5s 再转 idle（STORY_ERROR_HOLD_MS，back/reset
    清定时器，fake-timers 边界测试）。
  - F5 准备期取消不反弹：cancel 只投指令不检查 → 生成完成仍 story_begin+开播把已回首页
    前端弹回 → cancel 置旗 + story_begin 前/_speak(1) 前双检查（直接 story_end{cancelled}）。
- **遗留 backlog（终审裁决，后续打磨不阻塞）**：
  1. 意图正则主题质量：指示代词停用词（「我想听这个故事」→「这个」）、`_THEME_STRIP`
     两端剥字符伤合法主题（「一只猫」→「只猫」）、`search` 非锚定可致乱码主题——
     均为「产次品故事」方向（非抢问答），符合宁漏勿抢，下次打磨一行级修复。
  2. 死配置三字段：`story_min_scenes/max_scenes/scene_max_chars` 无消费方（代码钉死
     6/10/80 与默认值一致，无行为偏差）——接线或标注预留。
  3. 象限③（故事态新故事 ask）`_on_story_event` 无实例同一性守卫——前端故事态无输入
     入口不可达，防御性记录。
  4. `story_error` 若未来在 playing 阶段出现（当前仅 preparing 发）持留期无盖层展示——
     协议现状不可达。
  5. Windows GBK 控制台 smoke_story.py 中文 print 乱码（仅显示层，落盘 UTF-8 完好）。
- **测试**：pytest **829 passed** / vitest **102 passed** / npm run build 通过（终审复测）。
- **部署待办（运维侧）**：服务器正式池 `data/kiosk/preset_questions.json` 追加
  「给我讲个嫦娥奔月的故事」（正式池优先于缺省池）；KIOSK_STORY_* 全有默认值零配置开箱；
  冒烟 `python scripts/smoke_story.py "主题" --pages 2` + 全链
  `python scripts/smoke_kiosk_ws.py --port 7862 "给我讲一个霸王别姬的故事"`。

### 增补：web-064/065/066 用户验收反馈修复（2026-08-21）

- **web-064 分镜字数与等图翻页（用户实测反馈「连续多页无插画」）**：①分镜字数收为
  40~80——prompt 下限 + 首轮短段校验重试、重试后仍短接受+告警（等图机制兜底）；
  ②自动翻页加第三条件「当页插图已了结」（就绪或失败落地），短分镜播报快于生图时
  不再连跳无图页；插图最终失败补发 `story_page_img{failed:true}`（手动翻页不受限）；
  失败页占位文案「插画暂时走失了…」。
- **web-065 生图费用止血（用户验收反馈）**：取消/返回后未完成的插图任务不再重试、
  不再新发起——`should_stop` 取消旗标贯穿 ImageClient 重试链与 StorySession 编排。
- **web-066 deadline 跳页补事件（复审 Important 边角）**：总预算 300s 跳页此前静默
  return，等图护栏下该页 imgDone 永不达成→自动翻页冻结（末页连 story_finish 都发不出）；
  现跳页且未取消时同形补发 `failed:true`（与 web-064 失败补事件同路径）。
- **测试**：pytest **829 passed**（+1：deadline 跳页补事件，串行并发+时钟注入确定性
  构造）；vitest **102 passed**（+4：等图护栏三用例+失败页占位文案）；npm run build 通过。

### 增补：web-067 用户验收四问题修复（2026-08-21，真实 API 探测驱动）

用户验收报 4 问题：插图大面积缺失（4~5/10）、首页等待 30s+、寓言过度发挥背离原著、
无图自动翻页。本地真实 API 探测定位：

- **插图缺失根因=429 Throttling.RateQuota**：qwen-image-3.0 并发>2 即限流——探测实证
  并发 4 时 8/10 张 0.2s 秒拒、并发 2 时 10/10 全成（单张 6~10s、整墙 37s）。原配置
  注释「RPM=20 并发上限 4」系理论值，未实测。修复：**并发默认 4→2** +
  **ImageClient 限流退避重试**（限流错退避 rate_wait_s=6s 重试 ≤3 次，0.5s tick 可
  中断；非限流错立即重试 ≤1 次原语义；两类计数独立；should_stop 在尝试前与退避期间
  双检查）。「无图自动翻页」为此次生问题（failed 事件早到→等图护栏放行），根因消除
  后不再出现，无新代码。
- **首页 30s+ 根因=脚本 21.2s**（qwen-plus 默认开 thinking 隐式推理）+ 图 ~10s：
  探测 `enable_thinking=False` → **10.9s**，分镜 40~59 字全合规。再加**首页并行预生成**
  （C4）：脚本 ~11s 期间首页用主题 prompt 并行生成（t≈7~10s 落地），首屏「文字+插画」
  合计 ≤12s（生图 6~10s 在阿里侧为物理下限）；失败由页 1 worker 落回 scene prompt
  重生成；`KIOSK_STORY_FIRST_IMAGE_FAST=false` 可关。**实现纠偏**：规范原稿由预生成
  线程直发 img 事件，但事件必然早于 story_begin 到达（图 7~10s < 脚本 11s），会被
  前端 begin 清表抹掉→页 1 永无图且自动翻页冻结；改为页 1 worker 等预生成结果后
  统一发事件（worker 必在 begin 之后运行），单发不双发。
- **寓言忠实**：探测 qwen-flash（守株待兔→主角改小兔子）与 qwen-turbo（→小男孩+
  说教尾巴）均背离原著，且 flash 有 JSON 控制字符解析失败、两者分镜 20~38 字不达标
  ——**弃用提速换模型路线**；qwen-plus + prompt 忠实条款（「必须严格沿用原著的情节
  脉络、角色与结局……适龄化柔化」）实测主线全对（农夫/撞桩/守株/田荒/醒悟）+
  参考长度示例（首轮即达 40~80 字，避免重试倍增时延）。
- **测试**：pytest **841 passed**（+12：限流退避 5 + 预生成 5 + 提速/忠实 2）；
  vitest **102 passed**（纯服务端批次，防回归）；npm run build 通过。
- **部署提醒**：服务器 .env 若显式钉 `KIOSK_STORY_IMAGE_CONCURRENCY=4` 需改 2 或删除
  （走新默认值）；重传 `kiosk_server/story.py` + `kiosk_server/config.py` 并重启生效。

**web-068 评审修复轮**（scoped review verdict: Needs fixes → 全部 ADDRESSED）：
①prompt 错字「嫦娥→嫦娥」（探测稿逐字条款违反，测试补钉示例列举全句防再回归）；
②README 旧句「并发 ≤4/重试 1 次」同步为新口径；③页 1 worker 等预生成落地后补
deadline 重查（病态场景不再发起超预算 fallback，补 failed 事件防护栏冻结）+1 测试。
pytest **842 passed**。

**真实 API 端到端自测**（scripts/_selftest_story_e2e.py，真件 ScriptClient/ImageClient
+ 慢速假 TTS，主题「守株待兔」）：story_preparing 0.0s → story_begin **11.6s**（原 30s+）
→ 首页插图事件 11.6s（预生成命中）→ 插图 **10/10 全成 0 失败**（原缺 4~5 张）；寓言主线
忠实（农夫/撞桩/守株/田荒/醒悟，分镜 43~64 字全在 40~80 区间）。

### 增补：web-069 插图质量与可读性修复（2026-08-21，A/B 实测驱动）

用户验收报 3 问题：插图角落乱码文字、兔子三只耳朵（解剖错误）、文字/页数低对比。

- **乱码文字根因=引语入 prompt**：分镜文字含「…」/‘…’引语时，图像模型必渲染对话框
  文字（A/B/C 三路线实测：prompt 内否定句、negative_prompt 参数、双强化均压不住）。
  **根治=`strip_dialogue_for_image`**：引语连同言语动词（说/喊/问…）整段剥除，仅留
  动作描述；剥光回退原文。初版改写「说着话」仍诱发空对话框+问号符号→改整段剥除，
  实测对话场景出图零文字零气泡。**第二层防线**=`negative_prompt` 专用参数（文字/水印/
  畸形/多余肢体/多余耳朵/五官错位，同时覆盖非引语来源的随机伪文字与解剖错误——
  三只耳朵问题；扩散模型解剖错误不可 100% 消除，已双层减负并如实记录）。
- **文字/页数对比度**：`.story-text` 深棕→亮羊皮纸白 #fff7e6 + 双层深影描边 + 600 字重；
  `.page-indicator` 加羊皮纸胶囊底（同按钮语言）。
- **验证**：pytest 849（+7：剥引语 5 + negative_prompt 下发 + prompt 集成）/ vitest 102 /
  build ✓；4 张生产路径验证图逐张目检——零文字、零气泡、解剖正常。
- **部署提醒**：重传 `kiosk_server/story.py` + 前端构建（或 dev 热更）；**服务器旧缓存
  故事的图是旧 prompt 产物（含乱码图），需清 `data/story/` 对应目录或换主题才见新图**。

### 增补：web-070 插图文字根治/脚本换型/连字拼音（2026-08-21，探测驱动）

用户验收报 3 问题：①插图仍大量文字（含错字）；②主旨偏离熟知版本（农夫与蛇被改成蛇道谢）
+拍板脚本模型 qwen-plus→**deepseek-v4-flash-0731**；③键盘拼音太小+只能拼单字需连字拼音。

- **插图文字根因（再定位）**：qwen-image-3.0 文字渲染能力强——prompt 里的大段叙述 prose
  会被当文字内容画进图里（34/35 截图实证；引语剥除后 prose 主体仍会触发）。**根治=脚本
  为每分镜产出 15~25 字 images 纯画面短句**（谁/在哪里/做什么，无对话无引号无书名号），
  生图只喂短句；首页预生成 prompt 含《主题》会被渲染成书法标题（实测）→ **预生成默认关**
  （KIOSK_STORY_FIRST_IMAGE_FAST=true 可开回），页 1 于 begin 后由 images[0] 生成。
  strip_dialogue_for_image 与 negative_prompt 保留为纵深防御。
- **脚本换型**：dashscope Generation.call 原生支持 deepseek-v4-flash-0731（百炼 key 不变，
  实测连通 1.6s）；enable_thinking=False + 新 prompt（「严格按照大家熟知的主流版本…不得
  反转寓意（例如农夫与蛇的结局必须是蛇咬了农夫）」+images 字段要求）实测 **8.7s** 出全量
  脚本（较 qwen-plus 10.9s 更快）、农夫与蛇主线全对、分镜 44~58 字合规。端到端自测
  （农夫与蛇）：begin 13.7s（含 1 次校验重试）、插图 9/9 全成 0 失败、出图逐张目检零文字。
  max_tokens 1600→2200。解析：images 与 scenes 逐对 clamp 对齐、缺失/不齐回退 None
  （worker 用 scene 剥引语兑底）；story_begin 载荷保持 {n,text} 不泄漏 img。
- **连字拼音**：新 pinyinEngine.ts（词库候选优先+单字最长音节前缀，DP 音节切分）；
  词库 frontend/src/assets/pinyin_words.json（**2.1MB 内置**，jieba 词频×pypinyin 注音，
  生成器 scripts/build_pinyin_dict.py，79,993 keys）；pinyinKeyboard 改写自绘候选条+
  committed/buffer 自管模型（空格=选首选，IME 惯例）；键盘按键 108px/42px、候选 48px 放大。
  bundle 2,966KB（gzip 1,230KB，离线 kiosk 一次性加载可接受）。
- **测试**：pytest **856 passed**（+7）；vitest **112 passed**（+8 引擎 +2 连字组件用例）；
  build ✓。偶发：一次端到端自测出现 story_error（两轮校验均未过的随机事件，未复现），
  已在 story_error 前补服务端 logger.warning 留痕。
- **部署提醒**：重传 kiosk_server/{story,config}.py + scripts/smoke_story.py 并重启；
  前端 dev 热更；**服务器 data/story/ 旧缓存需清空**（旧图含文字、旧脚本无 images 字段）。

### 增补：web-071 键盘 UX 四轮反馈（2026-08-21）

用户报 4 点：候选词多行把键盘挤出屏/需首字母联想（nh→你好）/按钮与输入框不高亮/边界越对话框。

- **单行候选+更多浮层**：候选条 nowrap+定高 104px；splitRow 宽度估算（50·len+80，对齐实际
  48·len+72 上浮冗余；溢出时预算 750 给「更多▼」留位）；更多候选进 absolute 浮层
  （bottom:calc(100%+12px)，不占布局流→键盘永不被推挤），选词/击键自动收起。
- **首字母联想**：词库 JSON 重构 {words,initials}（initials=按字首字母索引，19,513 keys）；
  引擎规则=可完整切分音节→全拼词+单字，不可切分→首字母词优先；BOOST_WORDS 会话/儿童
  高频提权（jieba 新闻语料词频「南海」压「你好」→提权后你好居首，对齐用户示例）。
- **样式**：键面 #fffdf6 亮底+描边+投影+600 字重；功能键分色（完成 #4a7c59 白字）；
  输入框 40px/600；面板 width:100%+box-sizing 收拢边界。
- **评审修复（3 Critical 全闭合）**：①浮层被 .keyboard-panel overflow:hidden 裁剪→移除
  （横向约束已由 width/box-sizing/min-width 保证）；②destroy 漏清 overlay（第三个自绘 DOM）
  →补齐+测试断言；③行宽估算低估致「更多▼」自裁→估算上浮+预算收窄。M1 击键收起浮层、
  M2 包含块 CSS 钉住，一并修复。
- **测试**：vitest **118 passed**（首字母/更多浮层/连打余字母/清空无残留等组件级覆盖）/
  build ✓（bundle 2745KB gzip 1396KB）/ pytest 856（服务端零改动防回归）。

### 增补：web-072 单字母候选+首页叠标+输入行样式（2026-08-21）

- **单字母即出候选**：词库新增 letters 段（jieba 单字词频按拼音首字母归组，每字母 12 字，
  会话高频提权）；引擎单字母（非音节字母）→ 高频字候选（h→和/好/会…，a/e/o 本身是音节
  走原路径）。+2 测试（引擎+组件）。
- **首页左上角叠标**：根因=v1/bg.png 背景图已内嵌馆标，HomeView 再叠 `<img class="logo">`
  （46/46/115px）即重影——删除该 img 与 CSS（截图红箭头实证）。
- **输入行重设计**：弃用内嵌「拼音」文字的丑素材图（key_keyboard.png）与暗色 input_bg.png——
  mic-toggle 改高亮羊皮纸胶囊按钮（micro.png 图标+「语音」文案，与 VoiceBar 键盘钮对称语义）；
  输入框改 #fffdf6 亮底+3px 描边+内外阴影胶囊。
- vitest **120 passed** / build ✓（pytest 856 服务端零改动）。
