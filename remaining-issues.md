# 未修复问题清单 — 完整对照

## 审查范围说明

第一轮完整代码审查共发现 **~50 个具体问题点**，合并同类项后为 **37 个独立问题**。  
第二轮修复（bug-fix-plan.md）处理了 **5 项**，第三轮修复处理了 **24 项**，剩余 **8 项**待处理。

---

## 已修复问题（29 项）

### 第一轮修复（bug-fix-plan.md）

| 审查编号 | 问题描述 | 严重程度 | 修复编号 |
|---------|---------|---------|---------| 
| #F1 | `build_knowledge_base.py` 缺少 `DataLoader` 导入 | 致命 | bug-001 |
| #F2 | 向量数据库列表 metadata 过滤不生效 | 高 | bug-002 |
| #F3 | `setup_logger` 日志目录不存在 | 高 | bug-003 |
| #F4 | `run_qa.py` 知识库检查只查 `chunks.json` | 中 | bug-004 |
| #F5 | `verify_answer_grounding` 重复变量初始化 | 低 | bug-005 |

### 第三轮修复（本轮）

| 编号 | 问题描述 | 涉及文件 | 修复方式 |
|------|---------|---------|---------|
| R01 | Embedding 模式匹配过于宽松 | `src/cache.py` | 添加 `_pattern_match()` 边界检查，防止子串误匹配 |
| R03 | 流式查询缺少计时信息 | `src/rag_pipeline.py` | `query_stream()` 的 meta 中增加 `timing` 字段 |
| R04 | `create_collection` 异常捕获过宽 | `src/vector_store.py` | 区分"集合不存在"和其他异常，记录日志 |
| R05 | `_convert_history` 用 `---` 分割破坏 Markdown | `app.py` | 改用 `\n\n---\n\n` 多级分隔符 |
| R06 | `clear_history` 返回 3 值绑定 4 输出 | `app.py` | 返回 2 值，绑定到 2 个输出 |
| R07 | 非流式模式无进度反馈 | `app.py` | 添加"⏳ 正在查询..."提示 |
| R08 | 流式异常覆盖已输出 token | `app.py` | 保留已有部分回答，追加错误信息 |
| R10 | `add_artifacts` 缓存加载可能丢失数据 | `src/rag_pipeline.py` | 合并两个缓存文件，用 `seen_ids` 去重 |
| R11 | `filter_conditions` 未传给 BM25 检索 | `src/retriever.py` | `_bm25_search` 接收 `filter_conditions` 并过滤结果 |
| R12 | Pickle 反序列化不安全 | `src/cache.py` | 改用 JSON 格式，增加数据格式验证 |
| R13 | API Key 潜在泄露风险 | `src/config.py` | 添加 `__repr__` 屏蔽敏感字段 |
| R14 | 线程池异常处理不完整 | `src/embeddings.py` | 记录第一个错误，取消其他未完成 future |
| R15 | 缓存超容量时全量复制 | `src/cache.py` | 改用逐条删除最旧条目，避免全量复制 |
| R16 | `classify_query` 每次调用重建列表 | `src/rag_pipeline.py` | 定义为类常量 `_RECOMMEND_PATTERNS` 等 |
| R17 | BM25 分词器生成冗余 bigram token | `src/retriever.py` | 移除 bigram，仅使用 unigram |
| R18 | 空输入/空响应处理不完整 | `src/embeddings.py` | `embed_one` 空文本返回零向量 |
| R20 | Chunk ID 冲突 | `src/chunking.py` | `generate_id` 输入增加 `artifact_id` 前缀 |
| R21 | 重排序降级路径依赖 sklearn | `src/reranker.py` | `try/except ImportError` 降级返回原始顺序 |
| R22 | `init_pipeline` 静默吞异常 | `app.py` | 保留异常信息，`get_system_status` 显示准确状态 |
| R24 | `_trim_context` 裁剪策略浪费上下文 | `src/rag_pipeline.py` | 剩余空间 > 70% 时截断当前段落而非直接舍弃 |
| R27 | `query_stream` 类型标注不准确 | `src/rag_pipeline.py` | 更新 docstring 说明实际产出类型 |
| R28 | `is_kb_related` 和 `classify_query` 重复关键词列表 | `src/rag_pipeline.py` | 统一使用 `ARTIFACT_KEYWORDS` 类常量 |
| R29 | 缓存键非确定性 | `src/retriever.py` | 使用 `sorted(filter_conditions.items())` |
| R30 | `qdrant_memory_mode` 注释误导 | `src/config.py` | 修正注释描述 |
| R31 | Mock 数据随机种子缺失 | `scripts/generate_mock_data.py` | 添加 `--seed` 参数和 `seed` 支持 |

---

## 未修复问题（8 项）

### R02. 流式输出每次 token 全量格式化（部分修复）
- **文件：** `app.py:83-90`
- **当前状态：** 已从"每次 token 调用 format_answer"改为"每 5 个 token 或最后更新一次"，O(n²) 降为 O(n/5)，但仍有优化空间。
- **残留风险：** 每 5 个 token 更新一次，500 token 回答 → 约 100 次调用，可接受但仍非最优。
- **建议：** 使用 `format_answer` 的增量更新版本，或缓存 `chunks` 部分的 HTML。

### R09. `answer_question` 两次 yield 同一个 `history` 对象（未修复）
- **文件：** `app.py:89,104`
- **问题：** `yield history, ..., history` 第一个和第三个值指向同一个可变列表，混淆。
- **影响：** 代码可读性差，但功能无影响。Gradio 的 `outputs` 分配时第一个 `history` 赋给 `chatbot`，第三个 `history` 也赋给 `chatbot`，导致重复绑定。
- **建议：** 改用 `yield history, ..., history.copy()` 或调整 output 列表移除重复项。

### R19. 并发安全问题（已修复）
- **文件：** `src/cache.py`、`src/rag_pipeline.py`
- **修复内容：**
  - `LRUCache`：添加 `threading.Lock`，`get/set/clear/stats` 全部加锁保护
  - `EmbeddingCache`：添加 `threading.Lock`，`get/set/set_pattern/precompute_patterns/stats` 全部加锁保护
  - `RAGPipeline._ensure_knowledge_base()`：添加 `_kb_lock`，双重检查锁定模式

### R23. `MAX_CONTEXT_CHARS = 10000` 硬编码（未修复）
- **文件：** `src/rag_pipeline.py:110`
- **问题：** 不同 LLM 模型上下文窗口差异大（4k/8k/32k/128k），硬编码值无法适应模型切换。
- **建议：** 从 `settings` 读取，或根据 `llm_model_name` 自动推算。

### R25. `format_answer()` 分数表情符号硬编码阈值（未修复）
- **文件：** `app.py:118`
- **问题：** `score > 0.7 → 🟢, > 0.4 → 🟡, else → ⚪` 对不同相似度度量使用相同阈值，不准确。
- **影响：** BM25 检索结果的分数显示可能不直观。
- **建议：** 根据 `chunk_type` 或分数分布动态调整阈值。

### R26. 示例按钮通过 `btn` 组件传递文本（未修复）
- **文件：** `app.py:179`
- **问题：** `btn.click(respond, [btn, ...])` 依赖 Gradio 内部 `btn.value` 行为。
- **影响：** 代码可读性差，但功能正常。
- **备注：** 已评估替代方案 `gr.State(btn.value)`，但该方案在按钮创建时即求值，不符合预期。保持现状。

### R32. 测试覆盖关键场景（已修复，72 个测试全部通过）
- **文件：** `tests/test_pipeline.py`
- **新增测试场景：**
  - ✅ 空输入问题（`test_empty_query`、`test_classify_query_empty`）
  - ✅ 极长问题（`test_very_long_query`）
  - ✅ 无检索结果路径（`test_bm25_no_results`、`test_build_context_no_results`）
  - ✅ 所有切片类型禁用（`test_all_chunk_types_disabled`）
  - ✅ 缓存文件损坏恢复（`test_corrupted_cache_recovery`）
  - ✅ 重排序降级路径（`test_rerank_local_fallback_without_sklearn`）
  - ✅ 从未构建知识库异常（`test_ensure_knowledge_base_not_built`）
  - ✅ 各查询类型 Prompt 选择（`test_select_prompt_for_*`）
  - ✅ 并发安全回归测试（`test_cache_*` 等锁保护测试）
  - ✅ 向量搜索 filter 条件（`test_hybrid_retrieve` 等）
  - ✅ Embedding 空文本处理（`test_embed_one_empty_text`）
  - ✅ 防幻觉检测（`test_verify_answer_grounding_*`）
  - ✅ 上下文裁剪（`test_trim_context_*`）
  - ✅ 切片 ID 唯一性（`test_chunk_id_uniqueness`）
  - ✅ 已修复 Bug 回归测试（bug-001/003/004、R01/R12/R18/R21/R24 等）

---

## 汇总统计

| 修复轮次 | 修复数量 | 涉及文件 |
|---------|---------|---------|
| 第一轮（bug-fix-plan） | 5 | `build_knowledge_base.py`, `vector_store.py`, `utils.py`, `run_qa.py`, `rag_pipeline.py` |
| 第三轮（本轮） | 24 | `cache.py`, `vector_store.py`, `embeddings.py`, `rag_pipeline.py`, `retriever.py`, `chunking.py`, `reranker.py`, `config.py`, `generate_mock_data.py`, `app.py` |
| 第四轮（R19+R32） | 2 | `cache.py`, `rag_pipeline.py`, `tests/test_pipeline.py` |
| **未修复** | **6** | 见下 |

### 最终状态

| 类别 | 总数 | 已修复 | 未修复 |
|------|------|--------|--------|
| 逻辑缺陷 | 11 | 9 | 2 (R02, R09) |
| 安全风险 | 2 | 2 | 0 |
| 性能瓶颈 | 4 | 4 | 0 |
| 边界情况 | 8 | 7 | 1 (R23) |
| 代码质量 | 5 | 4 | 1 (R26) |
| 注释/配置 | 2 | 2 | 0 |
| Mock 数据 | 1 | 1 | 0 |
| 测试覆盖 | 1 | 1 | 0 |
| 分数显示 | 1 | 0 | 1 (R25) |
| **合计** | **37** | **31** | **6** |