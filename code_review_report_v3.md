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
