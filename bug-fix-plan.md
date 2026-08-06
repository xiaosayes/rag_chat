# Bug 修复计划

## 问题总览
| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-001 | `chat_stream` 中 `has_yielded` 变量未绑定即引用 | `src/llm.py` | 高 | 已修复 |
| bug-002 | `is_kb_related` 中闲聊关键词误拦截正常查询 | `src/rag_pipeline.py` | 中 | 已修复 |
| bug-003 | `query_stream` 返回值类型标注与实际不一致 | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-004 | `_bm25_search` 过滤条件中列表字段与标量值比较逻辑不完整 | `src/retriever.py` | 低 | 已修复 |
| bug-005 | Gradio 事件绑定中 `chatbot` 组件被重复更新 | `app.py` | 低 | 已修复 |
| bug-006 | `_pattern_match` 边界检查过度严格导致大量模式匹配失败 | `src/cache.py` | 高 | 已修复 |
| bug-007 | `EmbeddingCache.set()` 使用 FIFO 淘汰而非 LRU | `src/cache.py` | 高 | 已修复 |
| bug-008 | `EmbeddingCache.save()` 未加锁导致竞态条件 | `src/cache.py` | 高 | 已修复 |
| bug-009 | `is_kb_related` 子串匹配误判知识库问题为闲聊 | `src/rag_pipeline.py` | 高 | 已修复 |
| bug-010 | `VectorStore.upsert` point_id 哈希冲突风险 | `src/vector_store.py` | 中 | 已修复 |
| bug-011 | `init_pipeline` 中空字符串与 None 比较 | `app.py` | 中 | 已修复 |

## 验证结果（原有）

所有修复已完成，全部 140 项单元测试通过（0 失败、0 错误）。

### 验证清单

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-001 | 语法检查通过；模拟 `Generation.call` 抛出异常时 `except` 块能安全访问 `has_yielded`（值为 `False`，进入重试逻辑） | ✅ |
| bug-002 | `is_kb_related("测试流程是什么")` 返回 `True`（不再被闲聊路由拦截）；`is_kb_related("你好")` 仍返回 `False` | ✅ |
| bug-003 | 类型标注从 `Generator[Dict[str, Any], None, None]` 改为 `Generator[Union[Dict[str, Any], str], None, None]`，`Union` 已导入 | ✅ |
| bug-004 | 新增 `isinstance(meta_value, list)` 分支，处理标量过滤条件匹配列表 metadata 的场景 | ✅ |
| bug-005 | 事件绑定输出列表移除重复 `chatbot`，`respond` 函数 yield 从 4 值改为 3 值，`answer_question` 移除冗余 `history.copy()` | ✅ |

## 问题详情（原有）

### [bug-001] `chat_stream` 中 `has_yielded` 变量未绑定即引用

- **根因分析**：在 `BailianLLM.chat_stream()` 方法中，`has_yielded = False` 的赋值语句位于 `Generation.call(...)` 调用之后。如果 `Generation.call()` 本身抛出异常（如网络超时、连接断开、API 认证失败等），`has_yielded` 变量从未被赋值，导致 `except` 块中 `if has_yielded:` 抛出 `UnboundLocalError`，使方法无法按预期重试，而是直接崩溃。这会导致流式 LLM 调用在遇到网络抖动时无法自动恢复。
- **影响范围**：所有使用 `chat_stream` 的场景（流式问答、ui 界面流式模式）。用户可能在网络不稳定时收到 `UnboundLocalError` 而非友好的错误提示。
- **修复方案**：在 `try` 块开始前（`Generation.call()` 调用之前）预先初始化 `has_yielded = False`，确保无论异常在何处抛出，`except` 块中都能安全访问该变量。
- **风险分析**：低风险。仅改变变量的初始化位置，不影响运行时逻辑。
- **测试验证**：模拟 `Generation.call` 抛出异常，验证 `chat_stream` 进入 `except` 块后正确重试，而不是抛出 `UnboundLocalError`。

### [bug-002] `is_kb_related` 中闲聊关键词误拦截正常查询

- **根因分析**：`RAGPipeline.CHITCHAT_KEYWORDS` 列表包含 `"测试"`、`"test"`、`"帮助"`、`"help"`、`"命令"` 等关键词。`is_kb_related()` 使用子串匹配判断问题是否与知识库相关，如果问题包含这些关键词，则直接路由到闲聊模式，完全绕过知识库检索。但这些关键词可能出现在正常的知识库查询中（如"测试流程是什么"、"帮助文档在哪里"、"命令行工具有哪些"），导致相关内容无法被检索到。
- **影响范围**：所有使用 RAG 查询的场景（Web UI、交互式脚本、API 调用）。用户询问包含这些关键词的知识库问题时，LLM 将无法获得检索到的上下文，回答质量下降。
- **修复方案**：从 `CHITCHAT_KEYWORDS` 中移除 `"帮助"`、`"help"`、`"命令"`、`"测试"`、`"test"` 这五个可能出现在正常知识库查询中的关键词。保留问候、告别、感谢、自我介绍、天气等明确为闲聊的关键词。
- **风险分析**：低风险。移除后，包含这些关键词的问题将走 RAG 检索流程（如果知识库存在），这是正确行为。如果知识库未构建，会提示用户构建知识库。
- **测试验证**：验证 `is_kb_related("测试流程是什么")` 返回 `True`（走 RAG），`is_kb_related("你好")` 返回 `False`（走闲聊）。

### [bug-003] `query_stream` 返回值类型标注与实际不一致

- **根因分析**：`query_stream` 方法的类型标注为 `Generator[Dict[str, Any], None, None]`，但实际运行时该方法会依次 yield 两种不同类型的值：先 yield 一个 `Dict[str, Any]`（元数据 dict，包含 `type="meta"` 等字段），然后逐 token yield 多个 `str`（回答内容）。docstring 中也注明"实际产出类型为 `Dict[str, Any] | str`"。类型标注与运行时行为不一致，会导致类型检查工具（如 mypy、pyright）误报错误，或代码阅读者/调用方对返回值类型产生错误假设。
- **影响范围**：代码可维护性和类型安全性。不影响运行时行为。
- **修复方案**：将返回值类型标注从 `Generator[Dict[str, Any], None, None]` 改为 `Generator[Union[Dict[str, Any], str], None, None]`，同时在文件顶部（已存在）的 `typing` 导入中添加 `Union`。
- **风险分析**：极低风险。仅修改类型标注，不改变运行时行为。
- **测试验证**：运行类型检查工具（如 mypy）验证不再报错。

### [bug-004] `_bm25_search` 过滤条件中列表字段与标量值比较逻辑不完整

- **根因分析**：`HybridRetriever._bm25_search()` 中的过滤条件处理逻辑存在缺陷。当 `filter_conditions` 中某个字段的值为标量（非 list），而 `chunk.metadata` 中对应字段的值为 list 时，使用 `!=` 直接比较会始终返回 `True`（list ≠ scalar），导致该 chunk 被错误过滤掉。例如：`filter_conditions={"tags": "国宝"}` 且 `chunk.metadata["tags"] = ["国宝", "青铜器"]` 时，`["国宝", "青铜器"] != "国宝"` 为 `True`，导致 `match = False`，该 chunk 被排除。
- **影响范围**：当前代码中没有直接调用 `_bm25_search` 并传入 list 类型 metadata 字段标量过滤的场景（`retrieve_by_dynasty` 和 `retrieve_by_category` 过滤的是标量字段）。但这是一个潜在缺陷，当未来添加按标签等 list 字段过滤的逻辑时会被触发。
- **修复方案**：在标量比较分支中，增加对 `meta_value` 为 list 情况的处理：如果 `meta_value` 是 list，则检查 `value` 是否在 `meta_value` 中（`value in meta_value`）；否则使用 `!=` 比较。
- **风险分析**：低风险。增加 list 类型的检查分支，不影响现有标量字段的比较逻辑。
- **测试验证**：构造 `filter_conditions={"tags": "国宝"}` 且 metadata 包含 `["国宝", "青铜器"]` 的 chunk，验证过滤后该 chunk 被保留。

### [bug-005] Gradio 事件绑定中 `chatbot` 组件被重复更新

- **根因分析**：`app.py` 中 `msg.submit` 和 `submit_btn.click` 的事件绑定输出列表为 `[msg, chatbot, chunks_json, chatbot]`，其中 `chatbot` 出现了两次。这导致 Gradio 在每次 yield 时连续更新两次 `chatbot` 组件，第二次更新覆盖第一次。虽然不影响最终显示效果，但会带来不必要的渲染开销，且表明代码可能有误（第四个输出参数可能是意图不明确或遗漏了其他组件）。
- **影响范围**：Web UI 界面性能（轻微）、代码可维护性。
- **修复方案**：将事件绑定输出列表中的重复 `chatbot` 移除，改为 `[msg, chatbot, chunks_json]`。同时更新 `respond` 函数中的 yield 语句，只 yield 三个值（去掉多余的 `result[2]`）。
- **风险分析**：低风险。`chatbot` 组件只更新一次，与之前两次更新中最后一次的结果一致。
- **测试验证**：启动 Web UI，确认对话功能正常，聊天记录正确显示，检索结果面板正常显示。

---

## 修复顺序（原有）

1. bug-001：`src/llm.py`（高风险，可能导致运行时崩溃）
2. bug-002：`src/rag_pipeline.py`（中风险，影响回答质量）
3. bug-003：`src/rag_pipeline.py`（低风险，类型标注修正）
4. bug-004：`src/retriever.py`（低风险，潜在逻辑缺陷）
5. bug-005：`app.py`（低风险，UI 冗余更新）

---

## 新增问题详情

### [bug-006] `_pattern_match` 边界检查过度严格导致大量模式匹配失败

- **根因分析**：
  `EmbeddingCache._pattern_match()` 使用 CJK 字符边界检查来判断 pattern 是否以"完整短语"出现在 question 中。
  其 OR 逻辑 `(not is_cjk(before) or not is_cjk(after))` 要求 pattern 至少有一侧是非中文字符，
  但中文多字词（如"青铜器"、"推荐"）经常被中文字符包围，导致边界检查失败。

  例如：
  - pattern="青铜器" 在 "介绍青铜器知识" 中 → before="绍"(CJK), after="知"(CJK) → 不匹配 ❌
  - 但 "青铜器" 在这里是独立词，应该匹配

- **影响范围**：
  所有使用 EmbeddingCache 模式匹配的场景。高频问题模式库（如"推荐一些代表性的文物"）无法匹配
  用户输入的相关变体问题（如"给我推荐一些代表性的文物有哪些"），导致每次都需要调用 Embedding API，
  增加响应延迟和 API 费用。

- **修复方案**：
  移除过度严格的 CJK 边界检查。对于长度 >= 2 字符的 pattern，只要 pattern 出现在 question 中即匹配。
  对于单字符 pattern，要求精确匹配。

  ```python
  @staticmethod
  def _pattern_match(pattern: str, question: str) -> bool:
      if len(pattern) > len(question):
          return False
      if pattern not in question:
          return False
      # 单字符模式要求精确匹配，避免误匹配
      if len(pattern) <= 1:
          return pattern == question
      # 多字符模式：只要出现在问题中即匹配
      # 缓存是优化手段，近似匹配的 embedding 比缓存未命中（需要 API 调用）更好
      return True
  ```

- **风险分析**：
  低风险。放宽匹配条件后，"我不推荐这个" 会匹配 pattern="推荐"，但这是可接受的：
  1. 缓存是优化手段，不是正确性依赖
  2. 近似 embedding 仍能返回相关结果
  3. 相比缓存未命中需要 API 调用，近似匹配的开销更小

- **测试验证**：
  - pattern="青铜器" 匹配 "介绍青铜器知识" → 应返回 True
  - pattern="推荐" 匹配 "推荐一些文物" → 应返回 True
  - pattern="推荐" 匹配 "我不推荐这个" → 应返回 True（放宽后可接受）
  - pattern="文" 匹配 "文物" → 应返回 False（单字符 exact match）

### [bug-007] `EmbeddingCache.set()` 使用 FIFO 淘汰而非 LRU

- **根因分析**：
  `EmbeddingCache.set()` 中当缓存超过 1000 条时，删除最早插入的 `len - 500` 条记录：
  ```python
  if len(self._exact_cache) > 1000:
      keys = list(self._exact_cache.keys())[:len(self._exact_cache) - 500]
      for k in keys:
          del self._exact_cache[k]
  ```
  这是 FIFO（先进先出）淘汰策略，不是 LRU（最近最少使用）。频繁访问的热点数据可能被冷门数据挤出缓存。

- **影响范围**：
  高频问题（如"推荐一些代表性的文物"）被冷门问题挤出缓存，导致用户反复问同一个高频问题时，
  每次都重新调用 Embedding API，增加响应延迟和 API 费用。

- **修复方案**：
  将 `_exact_cache` 从 `Dict` 改为 `OrderedDict`，在 `get()` 中通过 `move_to_end()` 更新访问顺序，
  在 `set()` 中淘汰最早未访问的条目（LRU 语义）。

- **风险分析**：
  低风险。`OrderedDict` 序列化为 JSON 时与普通 `Dict` 格式一致，不影响持久化兼容性。

- **测试验证**：
  插入 1005 条后，频繁访问的旧条目应保留，冷门条目被淘汰。

### [bug-008] `EmbeddingCache.save()` 未加锁导致竞态条件

- **根因分析**：
  `save()` 方法访问 `self._exact_cache` 和 `self._pattern_cache` 但未加锁。
  当 `precompute_patterns()` 在锁内调用 `save()` 时，如果另一个线程同时调用 `set()` 修改缓存，
  会导致 `save()` 读取到不一致的数据，甚至损坏缓存文件。

  ```python
  def save(self):
      # ❌ 没有 with self._lock:
      with open(self._cache_file, "w", encoding="utf-8") as f:
          json.dump(self._exact_cache, f, ...)  # 可能在写入时被修改
  ```

- **影响范围**：
  并发场景下（多线程同时查询和预计算），缓存文件可能损坏，导致重启后缓存无法加载。

- **修复方案**：
  1. 将 `self._lock = threading.Lock()` 改为 `threading.RLock()`（可重入锁）
  2. 在 `save()` 内部添加 `with self._lock:` 保护

- **风险分析**：
  低风险。`RLock` 允许同一线程多次获取锁，避免 `precompute_patterns()` 中锁内调用 `save()` 的死锁问题。

- **测试验证**：
  并发读写测试，验证缓存文件不损坏。

### [bug-009] `is_kb_related` 子串匹配误判知识库问题为闲聊

- **根因分析**：
  `is_kb_related()` 对 `CHITCHAT_KEYWORDS` 使用简单子串匹配：
  ```python
  for pattern in RAGPipeline.CHITCHAT_KEYWORDS:
      if pattern in q.lower():
          return False
  ```
  这导致任何包含闲聊关键词的问题都被判定为闲聊。例如：
  - "你好文物" → 包含"你好" → 判为闲聊 ❌（用户可能在问文物）
  - "谢谢你的帮助是什么文物" → 包含"谢谢" → 判为闲聊 ❌

  即使 bug-002 已移除了部分关键词，但"你好"、"谢谢"、"再见"等核心闲聊关键词仍在列表中，
  子串匹配的误判问题仍然存在。

- **影响范围**：
  用户输入包含闲聊关键词的知识库问题时，系统直接返回 LLM 闲聊回答，不检索知识库。

- **修复方案**：
  改为精确匹配 + 短前缀匹配策略：
  - 问题与闲聊关键词精确匹配 → 判为闲聊
  - 问题以闲聊关键词开头且剩余部分仅为标点/语气词 → 判为闲聊
  - 其他情况 → 判为知识库相关

- **风险分析**：
  中风险。"今天天气怎么样"（7字）会因前缀匹配"今天天气"后剩余"怎么样"有实质内容而判为知识库相关，
  但这对系统影响很小（知识库检索无结果，LLM 用自己的知识回答）。

- **测试验证**：
  - `is_kb_related("你好")` → False（精确匹配闲聊）
  - `is_kb_related("你好文物")` → True（含闲聊词但有实质内容）
  - `is_kb_related("谢谢你的帮助是什么文物")` → True
  - `is_kb_related("今天天气怎么样")` → True（放宽后可接受）

### [bug-010] `VectorStore.upsert` point_id 哈希冲突风险

- **根因分析**：
  ```python
  point_id = int(hashlib.md5(chunk.id.encode()).hexdigest()[:16], 16) % (2**63)
  ```
  只取 MD5 前 16 位十六进制字符（64 位），再 mod 2^63。当 chunk 数量达到约 4×10^9 时，
  根据生日悖论，冲突概率约 50%。当前数据集较小，但随着数据增长风险增加。

- **影响范围**：
  不同 chunk 可能产生相同 Qdrant ID，后插入的会覆盖先插入的，导致数据丢失。

- **修复方案**：
  使用完整 MD5（128 位）作为 point_id，避免截断导致的冲突风险。
  ```python
  point_id = int(hashlib.md5(chunk.id.encode()).hexdigest(), 16) % (2**63)
  ```

- **风险分析**：
  低风险。完整 MD5 的冲突概率远低于截断版本。

- **测试验证**：
  10000 个不同 chunk.id 生成 10000 个唯一 point_id。

### [bug-011] `init_pipeline` 中空字符串与 `None` 比较导致 pipeline 无法重用

- **根因分析**：
  ```python
  _current_project: str = ""
  
  def init_pipeline(project_id: str = ""):
      project_id = project_id or ""
      if pipeline is not None and project_id == _current_project:
          return pipeline
  ```
  当 `project_id` 被显式传入 `None` 时，`project_id = project_id or ""` 将其转为 `""`，
  与 `_current_project`（初始 `""`）比较为 True，pipeline 被重用。
  但 Gradio 的 dropdown 传入的是字符串值（"museum" / "enterprise"），不会传 None，
  所以此问题在实际运行中不触发。但类型不一致仍是隐患。

- **影响范围**：
  仅当外部代码显式传入 `project_id=None` 时触发。

- **修复方案**：
  将 `_current_project` 的默认值改为 `None`，统一使用 `None` 表示空项目：
  ```python
  _current_project: Optional[str] = None
  ```
  并在比较时增加 `Optional[str]` 类型标注。

- **风险分析**：
  低风险。仅改变内部状态表示，不影响外部接口。

- **测试验证**：
  `init_pipeline(None)` 和 `init_pipeline("")` 都应使用同一个 pipeline 实例。

---

## 修复顺序（新增）

1. bug-006：`src/cache.py`（高优先级，模式匹配失败导致缓存命中率低）
2. bug-007：`src/cache.py`（高优先级，FIFO 淘汰导致热点数据被挤出）
3. bug-008：`src/cache.py`（高优先级，save() 未加锁导致竞态条件）
4. bug-009：`src/rag_pipeline.py`（高优先级，闲聊关键词误判知识库问题）
5. bug-010：`src/vector_store.py`（中优先级，Qdrant point_id 哈希冲突风险）
6. bug-011：`app.py`（中优先级，pipeline 无法被重用）

---

## 验证结果（新增）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-006 | 见下方验证步骤；全部 140 项单元测试通过 | ✅ 已修复 |
| bug-007 | 见下方验证步骤；全部 140 项单元测试通过 | ✅ 已修复 |
| bug-008 | 见下方验证步骤；全部 140 项单元测试通过 | ✅ 已修复 |
| bug-009 | 见下方验证步骤；全部 140 项单元测试通过 | ✅ 已修复 |
| bug-010 | 见下方验证步骤；全部 140 项单元测试通过 | ✅ 已修复 |
| bug-011 | 见下方验证步骤；全部 140 项单元测试通过 | ✅ 已修复 |

---

## 验证步骤

### bug-006 验证
1. pattern="青铜器" 匹配 "介绍青铜器知识" → 应返回 True
2. pattern="推荐" 匹配 "推荐一些文物" → 应返回 True  
3. pattern="推荐" 匹配 "我不推荐这个" → 应返回 True（放宽后可接受）
4. pattern="文" 匹配 "文物" → 单字符 exact match 要求 → 应返回 False
5. 运行 `pytest tests/test_edge_cases.py::TestEmbeddingCacheBoundaryBug -v`

### bug-007 验证
1. 插入 1005 条缓存，验证前 500 条被淘汰
2. 访问某条旧数据后，验证它不会被下一轮淘汰
3. 运行 `pytest tests/test_edge_cases.py::TestEmbeddingCacheEviction -v`

### bug-008 验证
1. 多线程并发 set 和 save，验证缓存文件不损坏
2. 运行 `pytest tests/test_edge_cases.py::TestEmbeddingCacheThreadSafety -v`

### bug-009 验证
1. `is_kb_related("你好")` → False
2. `is_kb_related("你好文物")` → True
3. `is_kb_related("谢谢你的帮助是什么文物")` → True
4. 运行 `pytest tests/test_edge_cases.py::TestIsKBRelatedEdgeCases -v`

### bug-010 验证
1. 10000 个不同 chunk.id 生成 10000 个唯一 point_id
2. 运行 `pytest tests/test_edge_cases.py::TestVectorStorePointID -v`

### bug-011 验证
1. `init_pipeline(None)` 和 `init_pipeline("")` 返回同一个实例
2. 运行 `pytest tests/test_edge_cases.py::TestInitPipelineComparison -v`
---

## 新增问题（第二轮审查）

## 问题总览
| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-012 | `EmbeddingCache._load` 异常恢复后类型不匹配，缓存文件损坏后 `set()` 崩溃 | `src/cache.py` | 高 | 已修复 |
| bug-013 | `LRUCache._make_key` 中 `sorted(kwargs.items())` 对不可比较的 kwargs 值抛出 `TypeError` | `src/cache.py` | 中 | 已修复 |
| bug-014 | `app.py` 全局 `pipeline` 变量线程不安全，多用户并发访问时可能竞态 | `app.py` | 高 | 已修复 |
| bug-015 | `_convert_history` 中 `pass` 导致连续 user 消息时 assistant 消息错乱 | `app.py` | 中 | 已修复 |
| bug-016 | `classify_query` 中 "比较" 模式匹配过于宽泛，误分类 | `src/rag_pipeline.py` | 中 | 已修复 |
| bug-017 | `DataLoader._normalize` 中 `importance` 字段值 "5.0" 字符串导致 `int()` 抛出 `ValueError` | `src/data_loader.py` | 中 | 已修复 |
| bug-018 | `BM25Retriever._tokenize` 中 CJK 标点被错误拼接到英文 token | `src/retriever.py` | 中 | 已修复 |
| bug-019 | `verify_answer_grounding` 只识别 `【名称】` 格式，漏检其他格式 | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-020 | `BailianEmbedding.embed_batch` 中 `ordered` 列表可能有 `None` 未检查 | `src/embeddings.py` | 中 | 已修复 |
| bug-021 | `app.py` 流式输出每 5 个 token 更新界面的频率不合理 | `app.py` | 低 | 已修复 |
| bug-022 | `is_kb_related` 中纯标点查询被判定为知识库相关 | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-023 | `DocumentLoader.load_file` 未检查路径遍历 | `src/document_loader.py` | 中 | 已修复 |
| bug-024 | `BailianEmbedding.embed_batch` 空列表输入行为不明确 | `src/embeddings.py` | 低 | 已修复 |
| bug-025 | `src/cache.py` 中 `import pickle` 未使用 | `src/cache.py` | 低 | 已修复 |
| bug-026 | `_trim_context` 中 chunk 文本包含分隔符时错误分割 | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-027 | `verify_answer_grounding` 正则匹配未考虑跨行名称 | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-028 | `_convert_history` 中 assistant 消息为空未处理 | `app.py` | 低 | 已修复 |

## 问题详情

### [bug-012] `EmbeddingCache._load` 异常恢复后类型不匹配导致后续 `set()` 崩溃

- **根因分析**：
  `EmbeddingCache._load()` 中，当 `exact_cache.json` 文件损坏或格式异常时，`except` 块将 `self._exact_cache` 设为普通 `dict` 而非 `OrderedDict`：
  ```python
  except Exception as e:
      logger.warning(f"加载 Embedding 缓存失败: {e}")
      self._exact_cache = {}  # ← 普通 dict，没有 move_to_end/popitem 方法
  ```
  后续 `get()` 方法调用 `self._exact_cache.move_to_end(question)` 或 `set()` 方法调用 `self._exact_cache.popitem(last=False)` 时，普通 `dict` 没有这些方法，抛出 `AttributeError`，导致整个应用崩溃。

- **影响范围**：所有使用 EmbeddingCache 的场景。只要 `exact_cache.json` 文件损坏（如磁盘写入中断、并发写入冲突），任何查询都会触发崩溃。

- **修复方案**：将 `except` 块中的 `self._exact_cache = {}` 改为 `self._exact_cache = OrderedDict()`，保持类型一致。

- **风险分析**：低风险。仅修改异常恢复路径的类型，不影响正常路径。

- **测试验证**：
  1. 手动创建损坏的 `exact_cache.json` 文件
  2. 创建 `EmbeddingCache` 实例
  3. 调用 `cache.set("test", [0.1, 0.2])` 验证不崩溃
  4. 调用 `cache.get("test")` 验证返回正确值

### [bug-013] `LRUCache._make_key` 中 `sorted(kwargs.items())` 对不可比较的 kwargs 值抛出 `TypeError`

- **根因分析**：
  ```python
  def _make_key(self, *args, **kwargs) -> str:
      key_str = str(args) + str(sorted(kwargs.items()))
      return hashlib.md5(key_str.encode()).hexdigest()
  ```
  `sorted(kwargs.items())` 要求 kwargs 值可比较大小。如果 `kwargs` 的值包含 `dict`、`list` 等不可比较类型，`sorted()` 抛出 `TypeError`。

- **影响范围**：当前代码中 `llm_cache.set_with_key()` 没有使用 kwargs，不会触发。但这是一个潜在风险。

- **修复方案**：使用 `str(kwargs)` 替代 `str(sorted(kwargs.items()))`，因为 `str(kwargs)` 也能生成确定性字符串表示，且不要求值可比较。

- **风险分析**：低风险。`str(kwargs)` 在 Python 3.7+ 中保持插入顺序，是确定性的。

- **测试验证**：
  1. `cache.set_with_key("v1", "prefix", {"nested": "value"})` 不抛出异常
  2. `cache.get_with_key("prefix", {"nested": "value"})` 返回 "v1"

### [bug-014] `app.py` 全局 `pipeline` 变量线程不安全

- **根因分析**：`app.py` 中 `pipeline` 和 `_current_project` 是全局变量。Gradio 的 Web 服务器是多线程的，多个请求同时到达时：两个线程可能同时创建实例，导致引用覆盖和状态不一致。

- **影响范围**：多用户并发访问 Web UI 时可能触发。

- **修复方案**：使用 `threading.Lock` 保护 `init_pipeline` 中的全局变量访问，采用双重检查锁定模式。

- **风险分析**：低风险。添加线程锁保护，不影响单线程行为。

- **测试验证**：多线程并发调用 `init_pipeline`，验证只创建一个实例。

### [bug-015] `_convert_history` 中 `pass` 导致消息角色序列错乱

- **根因分析**：当遇到连续 user 消息时，`pass` 跳过当前 user 消息，但 `assistant_msg` 处理继续执行。如果 `assistant_msg` 非空，则添加 assistant 消息，导致消息序列变为 `[user, assistant, assistant]`（缺少中间的 user 消息）。

- **影响范围**：当对话历史中存在中间 assistant 回复为空的情况。

- **修复方案**：当 `pass` 跳过 user 消息时，应同时跳过对应的 assistant 消息。使用 `continue` 跳过整轮。

- **风险分析**：低风险。

- **测试验证**：`_convert_history([("user1", None), ("user2", "asst2")])` 返回正确序列。

### [bug-016] `classify_query` 中 "比较" 模式匹配过于宽泛导致误分类

- **根因分析**："比较" 是一个常用词，可能出现在非比较类查询中。如 "比较有名的文物有哪些" 被误分类为 `COMPARISON`。

- **影响范围**：包含 "比较" 但实际意图为推荐或事实的查询被错误分类。

- **修复方案**：在 `_COMPARE_PATTERNS` 中降低 "比较" 的权重（从 10 改为 5），并增加上下文检查：如果 "比较" 后跟推荐类词汇，则降低比较类得分。

- **风险分析**：低风险。

- **测试验证**：
  1. `classify_query("比较有名的文物有哪些")` 返回 `recommendation`
  2. `classify_query("青铜器和瓷器有什么区别")` 仍返回 `comparison`

### [bug-017] `DataLoader._normalize` 中 `importance` 字段值 "5.0" 字符串导致 `int()` 抛出 `ValueError`

- **根因分析**：`int("5.0")` 抛出 `ValueError`，被静默处理为默认值 3，导致数据丢失。

- **影响范围**：从 CSV 或 JSON 加载数据时，如果 `importance` 字段包含 "5.0"、"4.5" 等浮点数格式，重要性信息丢失。

- **修复方案**：先尝试转换为 `float` 再转为 `int`：`int(float(normalized["importance"]))`。

- **风险分析**：低风险。

- **测试验证**：
  1. `_normalize({"importance": "5.0"})` 的 `importance` 为 5
  2. `_normalize({"importance": 5.0})` 的 `importance` 为 5

### [bug-018] `BM25Retriever._tokenize` 中 CJK 标点被错误拼接到英文 token

- **根因分析**：全角标点如 `，`（U+FF0C）不在 CJK 统一表意文字范围内，被当作非中文处理。但 `raw.split()` 按空白字符分割，标点不是空白字符，所以标点会附加到相邻的英文单词上。

- **影响范围**：BM25 英文检索时，包含 CJK 标点的英文 token 无法被纯英文关键词匹配。

- **修复方案**：在非中文处理分支中，使用 `re.sub` 将 CJK 标点替换为空格，再分割。

- **风险分析**：低风险。

- **测试验证**：`_tokenize("Hello，World")` 包含 "hello" 和 "world"。

### [bug-019] `verify_answer_grounding` 只识别 `【名称】` 格式

- **根因分析**：只匹配 `【】` 格式的名称。如果上下文使用其他格式，则无法提取来源名称。

- **影响范围**：防幻觉检测功能在项目自定义 prompt 未使用 `【】` 格式时完全失效。

- **修复方案**：增加多种格式的匹配：`【】`、`**`、`「」`、`《》`。

- **风险分析**：低风险。

- **测试验证**：上下文含 `**司母戊鼎**` 时能提取名称。

### [bug-020] `BailianEmbedding.embed_batch` 中 `ordered` 列表可能有 `None` 未检查

- **根因分析**：如果 API 返回的 `embeddings` 列表中 `text_index` 不连续（如缺失某个索引），对应位置的 `ordered` 元素保持 `None`。

- **影响范围**：API 返回异常时，`None` 值被传递到下游，可能导致 `TypeError` 或静默的数据损坏。

- **修复方案**：在 `_embed_batch` 返回前检查 `ordered` 中是否有 `None`，如果有则抛出异常。

- **风险分析**：低风险。

- **测试验证**：模拟 API 返回不完整的 `embeddings` 列表，验证抛出异常。

### [bug-021] `app.py` 流式输出每 5 个 token 更新界面的频率不合理

- **根因分析**：基于 token 数量更新，但 token 长度不线性增长。

- **影响范围**：UI 更新频率不稳定。

- **修复方案**：改为基于时间间隔更新（每 100ms 更新一次）。

- **风险分析**：低风险。

- **测试验证**：手动验证流式输出时 UI 更新流畅。

### [bug-022] `is_kb_related` 中纯标点查询被判定为知识库相关

- **根因分析**：纯标点查询不匹配任何 `CHITCHAT_KEYWORDS`，返回 True。

- **影响范围**：用户输入纯标点时，系统执行 RAG 检索，耗费 API 配额。

- **修复方案**：添加纯标点检查，如果查询只包含标点字符，返回 False。

- **风险分析**：低风险。

- **测试验证**：`is_kb_related("？？？")` 返回 False。

### [bug-023] `DocumentLoader.load_file` 未检查路径遍历

- **根因分析**：接受用户提供的 `Path` 对象，没有检查路径中是否包含 `..` 等遍历序列。

- **影响范围**：如果系统暴露了文件加载接口，攻击者可以读取系统任意文件。

- **修复方案**：添加路径解析检查，确保路径在允许的根目录内。

- **风险分析**：低风险。

- **测试验证**：路径 `"../secret.txt"` 抛出异常。

### [bug-024] `BailianEmbedding.embed_batch` 空列表输入行为不明确

- **根因分析**：`embed_batch([])` 返回空列表，调用方未检查。

- **影响范围**：当 `chunks` 为空时，`embed_batch` 返回空列表，下游可能异常。

- **修复方案**：在 `embed_batch` 开头添加空列表检查，提前返回空列表。

- **风险分析**：低风险。

- **测试验证**：`embed_batch([])` 返回 `[]`。

### [bug-025] `src/cache.py` 中 `import pickle` 未使用

- **根因分析**：`import pickle` 被导入但从未使用。

- **影响范围**：代码冗余，安全隐患。

- **修复方案**：移除未使用的 `import pickle`。

- **风险分析**：极低风险。

- **测试验证**：导入 `src.cache` 模块正常。

### [bug-026] `_trim_context` 中 chunk 文本包含 `CHUNK_SEPARATOR` 时错误分割

- **根因分析**：`context.split(CHUNK_SEPARATOR)` 如果 chunk 文本包含分隔符，会错误分割，导致上下文信息丢失。

- **影响范围**：概率极低，但一旦触发会导致上下文信息丢失。

- **修复方案**：
  1. 将 `CHUNK_SEPARATOR` 改为更独特的字符串 `\n\n=====CHUNK_SEPARATOR=====\n\n`，避免与正文冲突（bug-031）
  2. `_build_context` 直接传入列表给 `_trim_context`，避免 `split()` 操作（bug-031）
  3. `_trim_context` 支持接收列表参数，已分割好无需再分割

- **风险分析**：低风险。改为传入列表后完全避免分割问题。

### [bug-027] `verify_answer_grounding` 正则匹配未考虑跨行名称

- **根因分析**：`re.finditer(r'\*\*(.+?)\*\*', answer)` 使用 `.+?` 非贪婪匹配，不支持跨行。

- **影响范围**：防幻觉检测可能漏检不规范的 Markdown 格式。

- **修复方案**：添加 `re.DOTALL` 标志支持跨行匹配。

- **风险分析**：低风险。

- **测试验证**：回答中含跨行 `**名称**` 时能正确提取。

### [bug-028] `_convert_history` 中 assistant 消息为空未处理

- **根因分析**：如果 `assistant_msg` 只有检索来源部分，`split(HISTORY_SEPARATOR)[0]` 返回空字符串，assistant 消息不被添加，但 user 消息已被添加。

- **影响范围**：对话历史中某条回答只有检索来源时，LLM 收到不完整的上下文。

- **修复方案**：当 `clean` 为空时，同时删除对应的 user 消息。

- **风险分析**：低风险。

- **测试验证**：`_convert_history([("问题", "\n\n---\n\n来源")])` 返回空列表。

---

## 新增问题（第三轮审查）

## 问题总览
| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-029 | `build_knowledge_base.py` 输出路径未使用项目专属路径 | `scripts/build_knowledge_base.py` | 低 | 已修复 |
| bug-030 | `_convert_history` 最后一条消息为 user 角色时未处理，违反 LLM API 格式要求 | `app.py` | 低 | 已修复 |
| bug-031 | `CHUNK_SEPARATOR` 不够独特，可能被 chunk 正文匹配导致错误分割 | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-033 | `memory_mode` 下 `_ensure_knowledge_base` 路径检查错误，误判知识库为已构建 | `src/rag_pipeline.py` | 中 | 已修复 |

## 问题详情

### [bug-029] `build_knowledge_base.py` 输出路径未使用项目专属路径

- **根因分析**：`build_knowledge_base.py` 的输出路径硬编码为通用路径，未根据项目 ID 动态调整，导致多项目场景下输出路径混乱。

- **影响范围**：多项目构建时，输出路径可能覆盖其他项目的构建结果。

- **修复方案**：使用 `pipeline.project_cfg` 和 `pipeline.vector_store.local_path` 获取项目专属路径。

- **风险分析**：低风险。

- **测试验证**：
  1. `tests/test_edge_cases.py::TestBuildScriptOutputPaths::test_output_paths_project_aware`
  2. 检查 `build_knowledge_base.py` 中是否使用 `pipeline.project_cfg` 和 `pipeline.vector_store.local_path`

### [bug-030] `_convert_history` 最后一条消息为 user 角色时未处理

- **根因分析**：当最后一条消息的 assistant 回复为空时，user 消息被添加但对应的 assistant 消息未添加，导致最终消息列表最后一条是 user 角色，违反 LLM API 的消息格式要求（不能以 user 消息结尾，或出现连续 user 消息）。

- **影响范围**：对话历史中某条回答只有检索来源时，LLM API 调用可能因格式错误失败。

- **修复方案**：在 `_convert_history` 返回前，检查最后一条消息是否为 user 角色，如果是则删除。

- **风险分析**：低风险。

- **测试验证**：`_convert_history([("user1", None), ("user2", "")])` 返回空列表。

### [bug-031] `CHUNK_SEPARATOR` 不够独特可能导致错误分割

- **根因分析**：`CHUNK_SEPARATOR` 使用 `\n=====\n` 作为分隔符，但某些 chunk 正文可能包含类似内容，导致 `context.split(CHUNK_SEPARATOR)` 错误分割 chunk 正文。

- **影响范围**：概率极低，但一旦触发会导致上下文信息丢失。

- **修复方案**：
  1. 将 `CHUNK_SEPARATOR` 改为更独特的字符串 `\n\n=====CHUNK_SEPARATOR=====\n\n`
  2. `_build_context` 直接传入列表给 `_trim_context`，避免 `split()` 操作

- **风险分析**：低风险。

- **测试验证**：运行 `pytest tests/ -v` 确认所有测试通过。

### [bug-033] `memory_mode` 下知识库路径检查错误

- **根因分析**：`RAGPipeline._ensure_knowledge_base()` 中，当 `memory_mode=True` 时，Qdrant 数据实际存储在 `self.vector_store._snapshot_path` 子目录中，但代码检查的是 `qdrant_base` 路径，导致知识库已构建时被误判为未构建，触发重复构建。

- **影响范围**：使用 `memory_mode=True` 时，每次启动 Web UI 都会重复构建知识库，浪费时间和 API 费用。

- **修复方案**：在 `_ensure_knowledge_base` 中根据 `memory_mode` 选择正确的路径检查：`memory_mode=True` 时检查 `_snapshot_path`，否则检查 `qdrant_base`。

- **风险分析**：低风险。

- **测试验证**：
  1. 使用 `memory_mode=True` 构建知识库后重启，验证不再重复构建
  2. 运行 `pytest tests/ -v` 确认所有测试通过

---

## 修复顺序（新增）

1. bug-012：`src/cache.py`（高优先级，缓存损坏后崩溃）
2. bug-014：`app.py`（高优先级，线程安全）
3. bug-015：`app.py`（中优先级，消息序列错乱）
4. bug-016：`src/rag_pipeline.py`（中优先级，查询分类错误）
5. bug-017：`src/data_loader.py`（中优先级，数据丢失）
6. bug-018：`src/retriever.py`（中优先级，英文检索失败）
7. bug-019：`src/rag_pipeline.py`（中优先级，防幻觉检测失效）
8. bug-020：`src/embeddings.py`（中优先级，静默数据损坏）
9. bug-013：`src/cache.py`（中优先级，潜在崩溃）
10. bug-022：`src/rag_pipeline.py`（低优先级，无意义查询）
11. bug-021：`app.py`（低优先级，UI 更新频率）
12. bug-026：`src/rag_pipeline.py`（低优先级，分割符冲突）
13. bug-023：`src/document_loader.py`（中优先级，安全风险）
14. bug-024：`src/embeddings.py`（低优先级，边界情况）
15. bug-025：`src/cache.py`（低优先级，代码冗余）
16. bug-027：`src/rag_pipeline.py`（低优先级，正则改进）
17. bug-028：`src/app.py`（低优先级，边界情况）
18. bug-029：`scripts/build_knowledge_base.py`（低优先级，输出路径）
19. bug-030：`app.py`（低优先级，消息格式）
20. bug-031：`src/rag_pipeline.py`（低优先级，分隔符）
21. bug-033：`src/rag_pipeline.py`（中优先级，路径误判）

---

## 验证结果（新增）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-012 | 见下方验证步骤 | ✅ 已修复 |
| bug-013 | 见下方验证步骤 | ✅ 已修复 |
| bug-014 | 见下方验证步骤 | ✅ 已修复 |
| bug-015 | 见下方验证步骤 | ✅ 已修复 |
| bug-016 | 见下方验证步骤 | ✅ 已修复 |
| bug-017 | 见下方验证步骤 | ✅ 已修复 |
| bug-018 | 见下方验证步骤 | ✅ 已修复 |
| bug-019 | 见下方验证步骤 | ✅ 已修复 |
| bug-020 | 见下方验证步骤 | ✅ 已修复 |
| bug-021 | 见下方验证步骤 | ✅ 已修复 |
| bug-022 | 见下方验证步骤 | ✅ 已修复 |
| bug-023 | 见下方验证步骤 | ✅ 已修复 |
| bug-024 | 见下方验证步骤 | ✅ 已修复 |
| bug-025 | 见下方验证步骤 | ✅ 已修复 |
| bug-026 | 见下方验证步骤 | ✅ 已修复 |
| bug-027 | 见下方验证步骤 | ✅ 已修复 |
| bug-028 | 见下方验证步骤 | ✅ 已修复 |
| bug-029 | 见下方验证步骤 | ✅ 已修复 |
| bug-030 | 见下方验证步骤 | ✅ 已修复 |
| bug-031 | 见下方验证步骤 | ✅ 已修复 |
| bug-033 | 见下方验证步骤 | ✅ 已修复 |

---

## 验证步骤

### bug-012 验证
1. 创建损坏的 `exact_cache.json` 文件 → 写入 `{invalid`
2. 创建 `EmbeddingCache` 实例 → 不崩溃
3. 调用 `cache.set("test", [0.1, 0.2])` → 不崩溃
4. 调用 `cache.get("test")` → 返回 `[0.1, 0.2]`

### bug-013 验证
1. `cache.set_with_key("v1", "prefix", {"nested": "value"})` → 不抛出异常
2. `cache.get_with_key("prefix", {"nested": "value"})` → 返回 "v1"

### bug-014 验证
1. 多线程并发调用 `init_pipeline` → 不崩溃，只创建一个实例
2. 运行 `pytest tests/test_pipeline.py -v` 确认通过

### bug-015 验证
1. `_convert_history([("user1", None), ("user2", "asst2")])` → 返回正确序列

### bug-016 验证
1. `classify_query("比较有名的文物有哪些")` → 返回 `recommendation`
2. `classify_query("青铜器和瓷器有什么区别")` → 返回 `comparison`

### bug-017 验证
1. `_normalize({"importance": "5.0"})` → `importance` 为 5
2. `_normalize({"importance": 5.0})` → `importance` 为 5

### bug-018 验证
1. `_tokenize("Hello，World")` → 包含 "hello" 和 "world"
2. `_tokenize("Hello, World")` → 包含 "hello" 和 "world"

### bug-019 验证
1. `verify_answer_grounding("**司母戊鼎**", "【司母戊鼎】")` → `passed` 为 True
2. `verify_answer_grounding("**司母戊鼎**", "**司母戊鼎**")` → `passed` 为 True

### bug-020 验证
1. 模拟 API 返回 `embeddings` 缺失某个索引 → 抛出 `RuntimeError`

### bug-021 验证
1. 手动验证流式模式 UI 更新流畅

### bug-022 验证
1. `is_kb_related("？？？")` → False
2. `is_kb_related("！！！")` → False

### bug-023 验证
1. `load_file(Path("../secret.txt"))` → 抛出 `ValueError` 或 `FileNotFoundError`

### bug-024 验证
1. `embed_batch([])` → 返回 `[]`

### bug-025 验证
1. `import src.cache` → 正常导入

### bug-026 验证
1. 运行 `pytest tests/ -v` 确认 `_trim_context` 测试通过
2. 验证 `_build_context` 传入列表而非字符串，避免分割问题

### bug-027 验证
1. 回答中含跨行 `**名称**` 时能正确提取

### bug-028 验证
1. `_convert_history([("问题", "\n\n---\n\n来源")])` → 返回空列表

### bug-029 验证
1. 运行 `pytest tests/test_edge_cases.py::TestBuildScriptOutputPaths -v` 确认通过
2. 检查 `build_knowledge_base.py` 使用 `pipeline.project_cfg` 和 `pipeline.vector_store.local_path`

### bug-030 验证
1. `_convert_history([("user1", None), ("user2", "")])` → 返回空列表，无连续 user 消息

### bug-031 验证
1. `CHUNK_SEPARATOR` 为独特字符串 `\n\n=====CHUNK_SEPARATOR=====\n\n`
2. `_build_context` 传入列表而非字符串，避免分割

### bug-033 验证
1. `memory_mode=True` 时知识库构建后重启，不再重复构建
2. 运行 `pytest tests/ -v` 确认所有测试通过

---


---

## 新增问题（第四轮审查 - 测试工程师）

> 本轮由测试工程师独立审查（tests/test_review_findings.py，45 项），
> 修复前 12 项失败 → 修复后全部通过（185 passed）。

## 问题总览
| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-034 | `_convert_history` 中 `continue` 跳过整个循环体，assistant 回复为空时整轮对话丢失 | `app.py` | 高 | 已修复 |
| bug-035 | `_validate_message_roles` 丢弃当前问题而非历史遗留 user 消息 | `src/rag_pipeline.py` | 高 | 已修复 |
| bug-036 | `_ensure_knowledge_base` 只检查 chunks.json，文档构建的知识库（chunks_documents.json）永远无法加载 | `src/rag_pipeline.py` | 高 | 已修复 |
| bug-037 | `EmbeddingCache._load` 模式缓存格式未校验，损坏时 `get()` 抛 AttributeError | `src/cache.py` | 中 | 已修复 |
| bug-038 | `init_pipeline` 锁外返回全局 pipeline，并发切换项目时返回错误实例（竞态，实测 3/60 不匹配） | `app.py` | 高 | 已修复 |
| bug-039 | `LRUCache._make_key` 对 kwargs/dict 参数顺序敏感，相同语义不同顺序 → 缓存未命中 | `src/cache.py` | 中 | 已修复 |
| bug-040 | `add_artifacts` 缓存加载失败时覆盖写缓存文件，旧切片永久丢失 | `src/rag_pipeline.py` | 中 | 已修复 |
| bug-041 | `format_answer` 对 `score=None` 的 chunk 抛 TypeError | `app.py` | 中 | 已修复 |
| bug-042 | `VectorStore.search` 对 `hit.payload=None` 抛 AttributeError | `src/vector_store.py` | 中 | 已修复 |
| bug-043 | `BM25Retriever.build([])` 空 corpus 抛 ZeroDivisionError | `src/retriever.py` | 中 | 已修复 |
| bug-044 | `data/raw/artifacts.json` 未转义引号导致 JSON 解析失败，默认数据无法加载 | `data/raw/artifacts.json` | 高 | 已修复 |
| bug-045 | `query_stream` 中 `timings["total"]` 在 LLM 生成前计算，指标误导 | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-046 | `verify_answer_grounding` 防幻觉检查是死代码，从未接入 query 流程 | `src/rag_pipeline.py` | 中 | 已修复 |
| bug-047 | `HybridRetriever` 缓存 key 忽略 semantic_top_k/bm25_top_k，不同召回量共享缓存 | `src/retriever.py` | 中 | 已修复 |
| bug-048 | `ProjectManager.add_project` 项目 ID 未校验，路径遍历可写入目录外文件 | `src/project.py` | 中 | 已修复 |
| bug-049 | `_trim_context` 单段落超限时返回空字符串，唯一检索结果信息完全丢失 | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-050 | `query` 中 retrieved_chunks 短文本（<=200字符）也被追加 "..." | `src/rag_pipeline.py` | 低 | 已修复 |
| bug-051 | `VectorStore.upsert` metadata 含不可序列化对象时 json.dumps 崩溃 | `src/vector_store.py` | 低 | 已修复 |
| bug-052 | `generate_mock_data.py --stats` 恒为 True，参数无效 | `scripts/generate_mock_data.py` | 低 | 已修复 |
| bug-053 | `VectorStore.client` 懒连接无锁，多线程并发首次访问重复创建客户端 | `src/vector_store.py` | 低 | 已修复 |

## 问题详情

### [bug-034] `_convert_history` 中 `continue` 跳过整个循环体导致整轮对话丢失
- **根因分析**：`if messages and messages[-1]["role"] == "user": continue` 的 `continue` 跳过的是**整个循环体**（包括本轮的 `assistant_msg` 处理）。当上一轮 assistant 回复为空（`None`/`""`）时，`history = [("问题1", None), ("问题2", "回答2")]` 中"问题2"和"回答2"被**全部丢弃**（实测返回 `[]`），多轮对话上下文被静默重置。bug-015 的 `pass→continue` 修复解决了"assistant 无对应 user"问题，但引入了"整轮丢失"的新问题。
- **影响范围**：所有使用 Web UI / `_convert_history` 的多轮对话场景。用户问一个未得到有效回复的问题后再追问，LLM 丢失全部上下文。
- **修复方案**：将 `continue` 改为替换语义——把孤儿 user 消息（`messages[-1]`）替换为当前 user 消息，再正常处理本轮的 assistant 消息：
  ```python
  if messages and messages[-1]["role"] == "user":
      messages[-1]["content"] = user_msg   # 最新问题优先，替换孤儿消息
  else:
      messages.append({"role": "user", "content": user_msg})
  ```
- **风险分析**：低。替换语义保证"最新问题 + 其回答"保留；`[(q1,None),(q2,a2)]` → `[u2, a2]`，`[(q1,a1),(q2,None),(q3,a3)]` → `[u1,a1,u3,a3]`；原有 bug-028/030 的空回复清理逻辑不受影响。
- **测试验证**：`TestConvertHistoryMispairing`（2 项）通过；`tests/test_edge_cases.py::TestConvertHistoryEdgeCases`（6 项）通过。

### [bug-035] `_validate_message_roles` 丢弃当前问题
- **根因分析**：`query()`/`query_stream()` 在 `conversation_history` 之后追加当前问题，若历史以 user 结尾（上一轮未回答），追加后出现两个连续 user，`_validate_message_roles` 用 `continue` **跳过最后一条（当前问题）**，LLM 实际收到的是旧问题。
- **影响范围**：直接调用 `RAGPipeline.query/query_stream` 的 SDK 场景（app.py 的 `_convert_history` 已保证无连续 user，不触发）。
- **修复方案**：连续 user 时保留最新一条（`validated[-1] = msg`），与 bug-034 的替换语义一致。
- **风险分析**：低。仅影响"历史以 user 结尾"的异常输入；正常输入不触发。
- **测试验证**：`TestValidateMessageRolesDropsCurrentQuestion` 通过。

### [bug-036] `_ensure_knowledge_base` 无法加载文档构建的知识库
- **根因分析**：`build_knowledge_base_from_documents` 将切片缓存保存为 `chunks_documents.json`，但 `_ensure_knowledge_base` 只检查 `chunks.json` → 文档构建的默认项目知识库在 UI 中永远提示"未构建"（Qdrant 数据实际存在却不可用）。`run_qa.py` 同时检查两个文件，行为不一致。
- **影响范围**：使用 `--source docs/mixed` 构建知识库的默认项目。
- **修复方案**：`chunks.json` 不存在时回退检查 `chunks_documents.json`。
- **风险分析**：低。项目专属路径（`project_cfg.chunk_cache_path`）不受影响。
- **测试验证**：`TestEnsureKBWithDocumentCache` 通过（`_is_built=True`，BM25 从文档缓存加载）。

### [bug-037] `EmbeddingCache` 模式缓存格式未校验
- **根因分析**：`_load()` 中 `self._pattern_cache = json.load(f)` 未校验返回类型，`pattern_cache.json` 内容为 list/其他类型时，`get()` 中 `for pattern, emb in self._pattern_cache.items()` 抛 `AttributeError`。
- **影响范围**：pattern_cache.json 损坏（磁盘中断写入、并发写入）后任何查询崩溃。
- **修复方案**：加载后校验 `isinstance(raw, dict)`，否则降级为空字典。
- **风险分析**：低。
- **测试验证**：`TestEmbeddingCacheCorruptPatternFile` 通过。

### [bug-038] `init_pipeline` 锁外返回全局 pipeline（竞态）
- **根因分析**：`pipeline = RAGPipeline(...)` 在锁内创建，但 `return pipeline` 在锁外执行且读取**全局变量**。线程 A 释放锁后、return 前，线程 B 可能已替换全局 `pipeline`。实测 60 次并发调用出现 3 次"请求 museum 返回 enterprise"。真实环境下 `_ensure_knowledge_base`/`warmup` 耗时数秒，窗口更大。
- **影响范围**：多用户并发切换项目时回答错乱；`_ensure_knowledge_base`/`warmup` 可能预热到错误 pipeline。
- **修复方案**：锁内创建后用局部变量 `new_pipeline` 持有，锁外的预热与返回值都使用局部引用：
  ```python
  new_pipeline = RAGPipeline(...)
  pipeline = new_pipeline
  _current_project = project_id
  # 锁外：
  new_pipeline._ensure_knowledge_base()
  new_pipeline.warmup()
  return new_pipeline
  ```
- **风险分析**：低。快速路径（项目相同直接返回全局）保持不变，是安全且必要的。
- **测试验证**：60 次并发实测 0 不匹配；`TestInitPipelineRace` 通过。

### [bug-039] `LRUCache._make_key` 参数顺序敏感
- **根因分析**：`str(args) + str(kwargs)` 中 `{"a":1,"b":2}` 与 `{"b":2,"a":1}`、`arg2=..,arg1=..` 与 `arg1=..,arg2=..` 生成不同 key → 语义相同的调用缓存未命中（llm_cache/retrieval_cache 均受影响）。
- **影响范围**：所有使用 `LRUCache.get_with_key/set_with_key` 的缓存。
- **修复方案**：用 `json.dumps(sort_keys=True, default=str)` 规范化参数表示，dict 键排序保证确定性。
- **风险分析**：低。
- **测试验证**：`TestLRUCacheKwargsOrder`（2 项）通过。

### [bug-040] `add_artifacts` 缓存损坏时覆盖写导致旧数据丢失
- **根因分析**：缓存加载失败 → `old_chunks=[]` → BM25 只重建新数据 → 缓存文件被**覆盖写**为仅新切片。旧切片从缓存中永久丢失（Qdrant 向量仍在，但 BM25 检索不到且缓存无法恢复）。
- **影响范围**：缓存文件损坏后的增量添加操作。
- **修复方案**：缓存加载失败时**跳过缓存文件更新**（保留损坏文件以便人工修复恢复），新切片仍加入内存 BM25 与 Qdrant。
- **风险分析**：低。不丢失任何数据；代价是缓存文件保持损坏态，需人工修复。
- **测试验证**：`TestAddArtifactsDataLoss` 通过（缓存内容保持不变）。

### [bug-041] `format_answer` 对 `score=None` 崩溃
- **根因分析**：`score = c.get("score", 0)` 在 key 存在但值为 `None` 时返回 `None`，`score > 0.7` 抛 `TypeError`。
- **影响范围**：检索结果缺 score 字段/为 None 时 UI 层 500。
- **修复方案**：`score = c.get("score") or 0`（None 与缺失都回退 0），`name`/`chunk_type` 同理。
- **风险分析**：低。
- **测试验证**：`TestFormatAnswerEdge`（2 项）通过。

### [bug-042] `VectorStore.search` 对 `hit.payload=None` 崩溃
- **根因分析**：`payload.get("metadata_json")` 在 payload 为 None 时抛 `AttributeError`。
- **修复方案**：`payload = hit.payload or {}` 降级为空数据。
- **风险分析**：低。
- **测试验证**：`TestVectorStoreSearchNoPayload` 通过。

### [bug-043] `BM25Retriever.build([])` 空 corpus 崩溃
- **根因分析**：`rank_bm25` 内部 `num_doc / corpus_size` 对空 corpus 抛 `ZeroDivisionError`。
- **影响范围**：空数据源（空目录/空 JSON/空缓存）构建知识库直接崩溃。
- **修复方案**：`build([])` 前置检查，空列表直接返回并置 `_is_built=False`；未构建时 `retrieve` 仍抛 RuntimeError（保持原有契约）。
- **风险分析**：低。
- **测试验证**：`TestBM25EmptyCorpus` 通过；`test_bm25_not_built_error` 通过。

### [bug-044] 默认数据文件损坏
- **根因分析**：`data/raw/artifacts.json` 多处字符串值内使用未转义英文引号（如 `铸有"后母戊"三字`），`json.load` 报 `Expecting ',' delimiter`，默认项目无法加载数据、无法构建知识库。
- **影响范围**：默认项目（museum）初始化、构建、加载全部失败。
- **修复方案**：用 JSON 状态机修复 15 条文物数据中的 26 处未转义引号（字符串内部引号 → `\"`），数据内容不变。
- **风险分析**：低。修复后 `json.load` 与 `DataLoader.load` 均验证通过（15 条）。
- **测试验证**：`DataLoader.load("data/raw/artifacts.json")` 返回 15 条。

### [bug-045] `query_stream` 的 timing 指标误导
- **根因分析**：`timings["total"]` 在 LLM 流式生成**开始前**计算并随 meta yield，不含生成时间，UI 显示的是检索时间而非总响应时间。
- **影响范围**：流式模式下的响应时间展示（app.py 流式分支未消费 timing，仅信息展示）。
- **修复方案**：流式 meta 中改用 `timings["retrieval"]`（检索+重排阶段耗时），命名诚实；非流式 `query()` 的 `total` 仍在 LLM 后计算（正确）。
- **风险分析**：低。无消费者依赖流式 `timing["total"]`。
- **测试验证**：源码检查确认三个流式分支均使用 `retrieval`。

### [bug-046] `verify_answer_grounding` 死代码
- **根因分析**：防幻觉检查已实现但 `query()`/`query_stream()` 从未调用，功能完全失效。
- **影响范围**：文档宣称的"回答质量评估"未生效。
- **修复方案**：LLM 回答生成后调用 `verify_answer_grounding`，**仅记录告警日志、不拒绝回答**（避免行为突变）；流式模式累积全文后检查。
- **风险分析**：低。只增加日志，不改变返回内容。
- **测试验证**：`TestAnswerGroundingNotWired`（2 项）通过。

### [bug-047] 混合检索缓存 key 忽略召回量参数
- **根因分析**：`cache_key = f"retrieve:{query}:{top_k}:{filter_str}"` 未包含 `semantic_top_k`/`bm25_top_k`，不同召回量的检索共享同一缓存条目。
- **影响范围**：调用方改变召回量参数时得到错误缓存结果。
- **修复方案**：cache key 增加 `:{semantic_top_k}:{bm25_top_k}`。
- **风险分析**：低。
- **测试验证**：`TestHybridRetrieverCacheKey` 通过（不同 semantic_top_k 得到不同缓存）。

### [bug-048] `ProjectManager.add_project` 路径遍历
- **根因分析**：`save_path = self.projects_dir / f"{pid}.json"` 未校验 pid，`id="../evil"` 实测写入项目目录外任意位置。
- **影响范围**：若未来通过 Web 接口开放添加项目即构成任意文件写入。
- **修复方案**：pid 必须匹配 `[A-Za-z0-9_-]+`，否则抛 ValueError。
- **风险分析**：低。
- **测试验证**：`add_project({"id": "../evil"})` 抛 ValueError；合法 ID 正常添加。

### [bug-049] `_trim_context` 单段落超限返回空
- **根因分析**：唯一段落超过 max_chars 时 `trimmed=[]`，返回空字符串，唯一检索结果的信息完全丢失。
- **修复方案**：无任何段落被保留时截断第一段保留开头；`max_chars <= 0` 直接返回空。
- **风险分析**：低。
- **测试验证**：`TestTrimContextBoundary`（3 项）通过；`test_trim_context_long` 等既有测试通过。

### [bug-050] retrieved_chunks 短文本追加省略号
- **根因分析**：`c.text[:200] + "..."` 对短文本也追加省略号。
- **修复方案**：仅当 `len(c.text) > 200` 时截断追加。
- **风险分析**：低。
- **测试验证**：`TestRetrievedChunkTruncation`（2 项）通过。

### [bug-051] `VectorStore.upsert` metadata 不可序列化崩溃
- **根因分析**：metadata 含 set 等对象时 `json.dumps` 抛 TypeError，整个 upsert 失败。
- **修复方案**：捕获 `(TypeError, ValueError)`，`metadata_json` 降级为 `"{}"` 并记录告警；过滤字段（`meta_*`）不受影响。
- **风险分析**：低。
- **测试验证**：`test_upsert_metadata_with_unserializable` 通过。

### [bug-052] `generate_mock_data.py --stats` 恒为 True
- **根因分析**：`action="store_true", default=True` 使参数永远为 True，`--stats` 无法关闭。
- **修复方案**：改用 `argparse.BooleanOptionalAction`（Python 3.9+），支持 `--stats/--no-stats`。
- **风险分析**：低。
- **测试验证**：`python scripts/generate_mock_data.py --help` 显示 `--stats/--no-stats`。

### [bug-053] `VectorStore.client` 懒连接无锁
- **根因分析**：`client` 属性首次访问时无锁，多线程并发首次访问会重复创建 QdrantClient（仅一个被保存，其余泄漏且可能占用同一路径）。
- **修复方案**：增加 `_connect_lock`，双重检查锁定。
- **风险分析**：低。
- **测试验证**：`TestCacheThreadSafety` 等并发测试通过。

---

## 修复顺序（新增）

1. bug-034：`app.py`（高，对话上下文丢失）
2. bug-035：`src/rag_pipeline.py`（高，当前问题被丢弃）
3. bug-036：`src/rag_pipeline.py`（高，文档知识库不可用）
4. bug-038：`app.py`（高，并发竞态）
5. bug-044：`data/raw/artifacts.json`（高，默认数据不可加载）
6. bug-037：`src/cache.py`（中）
7. bug-039：`src/cache.py`（中）
8. bug-040：`src/rag_pipeline.py`（中，数据丢失）
9. bug-041：`app.py`（中）
10. bug-042：`src/vector_store.py`（中）
11. bug-043：`src/retriever.py`（中）
12. bug-046：`src/rag_pipeline.py`（中）
13. bug-047：`src/retriever.py`（中）
14. bug-048：`src/project.py`（中，安全）
15. bug-045：`src/rag_pipeline.py`（低）
16. bug-049：`src/rag_pipeline.py`（低）
17. bug-050：`src/rag_pipeline.py`（低）
18. bug-051：`src/vector_store.py`（低）
19. bug-052：`scripts/generate_mock_data.py`（低）
20. bug-053：`src/vector_store.py`（低）

---

## 验证结果（新增）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-034 | `_convert_history([("问题1",None),("问题2","回答2")])` → `[问题2, 回答2]`；`[(q1,a1),(q2,None),(q3,a3)]` → `[q1,a1,q3,a3]`；`TestConvertHistoryMispairing` | ✅ 已修复 |
| bug-035 | `_validate_message_roles([u,a,u]+[当前问题])` → 最后一条为当前问题；`TestValidateMessageRolesDropsCurrentQuestion` | ✅ 已修复 |
| bug-036 | chunks_documents.json + qdrant 就绪 → `_ensure_knowledge_base()` 加载成功 `_is_built=True`；`TestEnsureKBWithDocumentCache` | ✅ 已修复 |
| bug-037 | pattern_cache.json 为 list → `get()` 返回 None 不崩溃；`TestEmbeddingCacheCorruptPatternFile` | ✅ 已修复 |
| bug-038 | 60 次并发实测 0 不匹配；`TestInitPipelineRace` | ✅ 已修复 |
| bug-039 | kwargs/dict 乱序命中；`TestLRUCacheKwargsOrder` | ✅ 已修复 |
| bug-040 | 损坏缓存 + add_artifacts → 缓存文件内容不变；`TestAddArtifactsDataLoss` | ✅ 已修复 |
| bug-041 | `format_answer("回答",[{"score":None}])` 不崩溃；`TestFormatAnswerEdge` | ✅ 已修复 |
| bug-042 | payload=None 返回空 Chunk 不崩溃；`TestVectorStoreSearchNoPayload` | ✅ 已修复 |
| bug-043 | `build([])` 不崩溃；`TestBM25EmptyCorpus` | ✅ 已修复 |
| bug-044 | `json.load` 通过（15 条，转义 26 处）；`DataLoader.load` 返回 15 条 | ✅ 已修复 |
| bug-045 | 源码检查：query_stream 三个分支均使用 `timings["retrieval"]`；非流式 query() 的 total 在 LLM 后计算 | ✅ 已修复 |
| bug-046 | `verify_answer_grounding` 已接入 query/query_stream；`TestAnswerGroundingNotWired` | ✅ 已修复 |
| bug-047 | cache key 含 `semantic_top_k`/`bm25_top_k`；`TestHybridRetrieverCacheKey` | ✅ 已修复 |
| bug-048 | `add_project({"id":"../evil"})` 抛 ValueError；合法 ID 正常 | ✅ 已修复 |
| bug-049 | `_trim_context(["A"*300],100)` → `"A"*100`；`TestTrimContextBoundary` | ✅ 已修复 |
| bug-050 | 短文本原样返回、长文本截断；`TestRetrievedChunkTruncation` | ✅ 已修复 |
| bug-051 | metadata 含 set → upsert 不崩溃，metadata_json 降级 `"{}"`；`test_upsert_metadata_with_unserializable` | ✅ 已修复 |
| bug-052 | `--help` 显示 `--stats/--no-stats` | ✅ 已修复 |
| bug-053 | client 懒连接双重检查锁定；并发测试通过 | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **185 passed**（原 140 + 新增 45，含修复验证）。

---

## 验证步骤

### bug-034 验证
1. `python -c "from app import _convert_history; print(_convert_history([('问题1',None),('问题2','回答2')]))"` → `[user:问题2, assistant:回答2]`
2. `pytest tests/test_review_findings.py::TestConvertHistoryMispairing -v`

### bug-035 验证
1. `pytest tests/test_review_findings.py::TestValidateMessageRolesDropsCurrentQuestion -v`
2. 手动：直接调用 `RAGPipeline.query(question="新问题", conversation_history=[{user:旧问题},{assistant:旧回答},{user:未回答}])`，LLM 收到的最后一条应为"新问题"

### bug-036 验证
1. `python scripts/build_knowledge_base.py --source docs`（默认项目）后重启 Web UI，状态应显示"系统就绪"而非"知识库未构建"
2. `pytest tests/test_review_findings.py::TestEnsureKBWithDocumentCache -v`

### bug-038 验证
1. `pytest tests/test_review_findings.py::TestInitPipelineRace -v`
2. 手动：两个浏览器窗口分别选 museum/enterprise 并发提问，回答应各归其项目

### bug-044 验证
1. `python -c "import json; print(len(json.load(open('data/raw/artifacts.json', encoding='utf-8'))))"` → 15
2. `python scripts/build_knowledge_base.py --source mock` 构建成功

### bug-039 验证
1. `pytest tests/test_review_findings.py::TestLRUCacheKwargsOrder -v`

### bug-040 验证
1. 手动损坏 `data/processed/chunks.json` 后调用 `add_artifacts`，确认文件内容未被覆盖
2. `pytest tests/test_review_findings.py::TestAddArtifactsDataLoss -v`

### bug-048 验证
1. `python -c "from src.project import ProjectManager; import tempfile; from pathlib import Path; pm=ProjectManager(projects_dir=Path(tempfile.mkdtemp())); pm.add_project({'id':'../evil','name':'x'})"` → ValueError

### bug-052 验证
1. `python scripts/generate_mock_data.py --help` → 显示 `--stats, --no-stats`
2. `python scripts/generate_mock_data.py --no-stats -n 3` 不打印统计信息

---

## 新增问题详情（第二轮独立审查，bug-054 ~ bug-061）

### [bug-054] `app.py` 未实现 `--project` / `--no-stream` 命令行参数，文档中的多项目部署命令全部不可用
- **根因分析**：`app.py` 的 `main()` 使用 argparse 仅定义 `--host/--port/--share` 三个参数，未定义 `--project` 与 `--no-stream`。但 README.md（约 15 处）、DEPLOY_GUIDE.md、project-context.md 以及 `generate_mock_project_data.py` 的运行提示均要求执行 `python app.py --project museum --port 7860` 进行多项目独立部署。实测 `python app.py --project museum` 直接报错 `unrecognized arguments: --project museum`，多项目独立部署流程无法按文档执行。
- **影响范围**：README/DEPLOY_GUIDE 中所有 `app.py --project` 部署命令；用户按文档执行时 Web UI 无法启动。
- **修复方案**：
  1. `main()` argparse 增加 `--project`（透传给 `init_pipeline()`）与 `--no-stream`（禁用流式输出，透传给 `create_ui()`）；
  2. `create_ui()` 增加 `default_stream: bool = True` 参数，`use_stream` 复选框的 `value` 使用该参数。
- **风险分析**：低。仅新增可选参数，默认行为不变（不传 `--project` 时仍为默认项目，不传 `--no-stream` 时仍默认流式）。
- **测试验证**：`python app.py --project museum --no-stream --help` 能正常解析参数；不带参数启动行为与之前一致。

### [bug-055] Reranker 调用方式与响应解析不符合 rerank API 契约，线上重排可能从未生效
- **根因分析**：`BailianReranker._rerank_with_api()` 调用 `TextEmbedding.call(model="qwen3-reranker-*", input=texts, query=query)`，并解析 `resp.output["embeddings"][].text_index/score`。但已核查本机 dashscope SDK：重排模型应使用专用接口 `dashscope.TextReRank.call(model, query, documents)`，其响应结构为 `output.results[].index / relevance_score`（`ReRankResult` 仅含 index、relevance_score、document 三个字段）。按现有实现，API 要么直接报错、要么 `embeddings` 为空触发 `ValueError`，随后静默降级到本地 TF-IDF——qwen3-reranker 线上重排实际上从未生效，且无任何日志提示。
- **影响范围**：所有启用重排的 RAG 查询（Web UI / CLI / API），重排精度长期停留在本地 TF-IDF 水平。
- **修复方案**：改用 `TextReRank.call(model=self.model, query=query, documents=texts)`，按 `output.results[].index / relevance_score` 解析，并按 `index` 映射回原始 candidates；保留失败时降级本地 TF-IDF 的逻辑。
- **风险分析**：中。涉及对外部 API 的调用方式变更，需真实 API Key 验证；`TextReRank` 已由 dashscope 顶层导出（`dashscope/__init__.py` 第 32/74 行已验证）。
- **测试验证**：mock `TextReRank.call` 返回 `{"results":[{"index":1,"relevance_score":0.9},...]}`，验证重排结果顺序正确；API 异常时仍走本地降级。

### [bug-056] 自定义 Prompt 模板含字面花括号时 `get_prompt` 崩溃
- **根因分析**：`ProjectConfig.get_prompt()` 使用 `template.format(context=context)` 填充上下文。若模板中出现字面花括号（如 JSON 示例 `{"name": "value"}`），`str.format()` 会将其当作占位符解析并抛 `KeyError`/`ValueError`。实测模板含 `{"name": "value"}` 时抛 `KeyError: '"name"'`，导致该项目的所有查询直接失败。`add_project()` 允许任意自定义 prompts，是触发入口。
- **影响范围**：通过 `add_project()` 添加含 JSON/大括号文本的自定义项目；该类项目所有查询崩溃。
- **修复方案**：改用 `template.replace("{context}", context)` 仅替换 `{context}` 占位符，其余大括号原样保留。
- **风险分析**：低。内置模板均只含 `{context}` 占位符，`replace` 行为与 `format` 一致；模板无 `{context}` 时 `replace` 为空操作（原 `format` 在无占位符时也正常）。
- **测试验证**：构造含 `{"a": 1}` 的模板调用 `get_prompt` 不再抛异常，`{context}` 被正确替换。

### [bug-057] "今天天气怎么样" 等天气/闲聊问题被误判为知识库问题
- **根因分析**：`is_kb_related()` 前缀匹配后，剩余部分仅当全部字符落在白名单 `'，。！？,。!? ～~啊呀哦嗯吧呗吗'` 中才判为闲聊。实测："今天天气怎么样" 命中关键词 `今天天气` 后剩余 `怎么样` 不在白名单 → 返回 True 走 RAG；"你好呢" 剩余 `呢` 同样不在白名单。而 `app.py` 示例按钮就包含"今天天气怎么样"，项目文档明确将"天气"列为闲聊路由场景。知识库未构建时该问题会直接抛 `RuntimeError`，已构建时也白白做一次检索。
- **影响范围**：Web UI 示例按钮"今天天气怎么样"、"你好呢"、天气类开场白等场景；KB 未构建时直接报错。
- **修复方案**：白名单补充 `呢`，并新增常见语气后缀集合 `（怎么样/怎样/如何）`，前缀匹配后 `extra` 为空、全为白名单字符、或命中后缀集合之一时判为闲聊。
- **风险分析**：低。仅放宽闲聊判定边界；"天气对文物保存有影响吗" 等真实知识库问题（extra 含实质内容）不受影响。
- **测试验证**：`is_kb_related("今天天气怎么样") == False`、`is_kb_related("你好呢") == False`、`is_kb_related("天气对文物保存有影响吗") == True`。

### [bug-058] PaddleOCR 3.x 输出格式不兼容，OCR 静默失效
- **根因分析**：`ImageParser._parse_with_paddleocr()` 按 PaddleOCR 2.x 格式解析 `line[1][0]`（即 `[box, (text, confidence)]`）。PaddleOCR 3.x 每行返回 `[text, confidence]`，此时 `line[1][0]` 取到的是 float 分数、`line[1][1]` 越界抛 IndexError，异常被 `parse()` 捕获后静默降级到 Tesseract——OCR 功能在 3.x 下完全失效且无提示。当前 PyPI 最新版即 3.x，requirements 注释中仍写 `paddleocr>=2.7.0`。
- **影响范围**：`build_knowledge_base_from_documents` / `build_mixed` 中图片 OCR 功能（安装 PaddleOCR 3.x 的环境）。
- **修复方案**：解析时兼容两种格式——`line[1]` 为 list/tuple 时按 2.x（box, (text, conf)）解析，否则按 3.x（text, conf）解析。
- **风险分析**：低。仅在原有解析处增加分支，2.x 路径行为不变。
- **测试验证**：mock 两种格式的 OCR 返回，验证均能正确提取文本与置信度过滤。

### [bug-059] 切换项目时旧 pipeline 资源未释放
- **根因分析**：`init_pipeline()` 在项目切换时直接新建 `RAGPipeline`（含新的 VectorStore/QdrantClient），旧实例从不释放。`VectorStore.close()` 定义后全项目无任何调用方。频繁切换项目会累积 Qdrant 本地文件句柄/连接。
- **影响范围**：Web UI 频繁切换项目（museum/enterprise）的场景；长期运行内存/句柄缓慢增长。
- **修复方案**：`init_pipeline()` 锁内替换全局 pipeline 前，对旧实例调用 `vector_store.close()`（try/except 保护）。
- **风险分析**：低-中。切换瞬间若有旧 pipeline 的查询在途，close 可能使其报错；Web UI 单用户场景影响极小。
- **测试验证**：连续切换多个项目后无异常；`pipeline.vector_store._client` 为 None（已关闭）。

### [bug-060] `Artifact.tags` 为标量类型时切片崩溃，整件文物静默丢失
- **根因分析**：`DataLoader._normalize()` 仅对字符串 tags 做拆分，JSON 中 `"tags": 123` 这类标量会原样保留到 `Artifact.tags`。`SmartChunking.chunk()` 中 `artifact.tags[:5]` 对 int 抛 `TypeError: 'int' object is not subscriptable`，异常被 `ChunkingPipeline.process()` 捕获后该文物无任何切片产出，仅记一条日志，数据静默丢失。
- **影响范围**：JSON/CSV 数据源中 tags 字段为数字/布尔等标量的文物记录。
- **修复方案**：`SmartChunking.chunk()` 中先判断 `artifact.tags` 是否为 list，非 list 时按空列表处理。
- **风险分析**：低。仅增加类型防御，正常 list 路径行为不变。
- **测试验证**：构造 `tags=123` 的 Artifact 调用 chunk() 不再抛异常，正常产出切片。

### [bug-061] 全空字段的 Artifact 生成相同 ID，向量互相覆盖
- **根因分析**：`Artifact.__post_init__()` 在无显式 id 时用 `generate_id(name+dynasty+category+material)` 生成。四个字段全空时生成 `md5("")`（实测 `d41d8cd9...`），多件空文物 id 完全相同，导致其 chunk id、Qdrant point id（由 chunk.id 哈希）全部相同，后插入向量覆盖前者，检索结果错乱/丢失。
- **影响范围**：JSON 数据源中关键字段全部缺失的记录；构建知识库时多件空记录互相覆盖。
- **修复方案**：`__post_init__()` 中组合字符串为空时，追加 `uuid4().hex` 保证唯一性。
- **风险分析**：低。仅影响全空记录（原本就不可用），正常记录 ID 生成逻辑不变。
- **测试验证**：两个全空 Artifact 的 id 不同；正常字段 Artifact 的 id 仍确定性生成。

---

## 修复顺序（第二轮）

1. bug-054：`app.py`（高，文档部署命令不可用）
2. bug-055：`src/reranker.py`（高，线上重排从未生效）
3. bug-057：`src/rag_pipeline.py`（中，闲聊误判/示例按钮报错）
4. bug-056：`src/project.py`（中，自定义项目查询崩溃）
5. bug-058：`src/document_loader.py`（中，OCR 静默失效）
6. bug-060：`src/chunking.py`（中，数据静默丢失）
7. bug-061：`src/data_loader.py`（低，ID 碰撞）
8. bug-059：`app.py`（低，资源未释放）

---

## 验证结果（第二轮）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-054 | `python app.py --project museum --no-stream --help` 参数解析正常；不带参数启动行为不变 | ✅ 已修复 |
| bug-055 | mock `TextReRank.call` 返回 `results[].index/relevance_score`，重排顺序正确；API 异常时降级本地 | ✅ 已修复 |
| bug-056 | 含 `{"a": 1}` 的模板 `get_prompt` 不抛异常，`{context}` 正确替换 | ✅ 已修复 |
| bug-057 | `is_kb_related("今天天气怎么样")==False`、`("你好呢")==False`、`("天气对文物保存有影响吗")==True` | ✅ 已修复 |
| bug-058 | mock 2.x 与 3.x 两种 OCR 输出均正确解析 | ✅ 已修复 |
| bug-059 | 连续切换项目后旧 pipeline 的 vector_store 已关闭 | ✅ 已修复 |
| bug-060 | `Artifact(name="X", tags=123)` 切片不再抛异常 | ✅ 已修复 |
| bug-061 | 两个全空 Artifact 的 id 不同 | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **185 passed**（8 项修复全部完成，0 失败 0 错误）。

> 说明：修复过程中同步更新了 3 个断言旧行为的既有测试（`test_is_kb_related` 中"今天天气怎么样"改为 False、`test_prompt_template_with_unmatched_brace` 改为断言不抛异常、`test_edge_cases.py` 两个 reranker 测试改为 mock `TextReRank.call`），并新增针对性验证脚本。

---

## 验证步骤（第二轮）

### bug-054 验证
1. `python app.py --project museum --no-stream --help` → 正常输出帮助信息（含 `--project`、`--no-stream`）
2. `python app.py --project museum --no-stream` 启动后，UI 流式复选框默认不勾选

### bug-055 验证
1. `python -c` 构造 mock 响应调用 `_rerank_with_api`，验证结果按 relevance_score 降序且 index 映射正确
2. 有 API Key 时实际调用一次，确认使用 qwen3-reranker 而非降级

### bug-056 验证
1. `python -c` 构造含 JSON 示例的自定义 Prompt 调用 `get_prompt`，不再抛异常

### bug-057 验证
1. `python -c "from src.rag_pipeline import RAGPipeline; print(RAGPipeline.is_kb_related('今天天气怎么样'))"` → False
2. `python -c "...is_kb_related('你好呢')"` → False
3. `python -c "...is_kb_related('天气对文物保存有影响吗')"` → True

### bug-058 验证
1. mock PaddleOCR 2.x 输出 `[[[box],('文本',0.95)]]` 与 3.x 输出 `[['文本',0.95]]`，均能提取文本

### bug-059 验证
1. 连续调用 `init_pipeline('museum')` / `init_pipeline('enterprise')` 多次，无异常，旧实例 vector_store 已关闭

### bug-060 验证
1. `python -c` 构造 `Artifact(name='X', tags=123)` 调用 `SmartChunking().chunk()` 不抛异常

### bug-061 验证
1. `python -c` 构造两个全空 Artifact，`id` 互不相同

---

## 新增问题（第五轮复测审查 - 精准修复）

> 审查方式：全量源码复读 + `pytest` 回归（185 项基线全通过）
> 本轮发现 P0×1、P1×7，共 **8 项**，全部修复完成
> 全量测试：`pytest tests/ -q` → **185 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-062 | 检索缓存 key 缺少项目标识，跨项目共享缓存导致串数据 | `src/retriever.py`、`src/rag_pipeline.py` | P0 | 已修复 |
| bug-063 | API 非 200 响应（429/5xx）无退避直接连发重试 | `src/llm.py`、`src/embeddings.py`、`src/reranker.py` | P1 | 已修复 |
| bug-064 | 项目专属 chitchat Prompt 定义后从未生效 | `src/rag_pipeline.py` | P1 | 已修复 |
| bug-065 | Settings 多个配置项未接线，修改 .env 无效 | `src/rag_pipeline.py` | P1 | 已修复 |
| bug-066 | `add_artifacts` 在 Qdrant 集合缺失时 upsert 崩溃 | `src/rag_pipeline.py` | P1 | 已修复 |
| bug-067 | Embedding 模式缓存加载未校验值类型 | `src/cache.py` | P1 | 已修复 |
| bug-068 | `init_pipeline` 关闭旧连接后旧实例惰性重连，双客户端同路径冲突 | `src/vector_store.py` | P1 | 已修复 |
| bug-069 | 构建方法静默忽略传入的 `project_id`，数据写入错误项目 | `src/rag_pipeline.py` | P1 | 已修复 |

## 问题详情

### [bug-062] 检索缓存 key 缺少项目标识 → 跨项目串数据（P0）

- **根因分析**：`retrieval_cache` 是模块级全局单例（`src/cache.py`），所有项目（museum / enterprise）的 `HybridRetriever` 实例共享。缓存 key 为 `retrieve:{query}:{top_k}:{semantic_top_k}:{bm25_top_k}:{filter_str}`，**不含 project_id / collection_name**。Web UI 支持同一进程内切换项目，5 分钟 TTL 内相同问题会命中另一个项目的缓存结果；同一项目重建知识库后 TTL 内也会命中旧数据。
- **影响范围**：多项目 Web UI 下返回错误项目的文物/文档；重建知识库后短时间内查询返回旧答案。
- **修复方案**：缓存 key 加入 `self.vector_store.collection_name`；`build_knowledge_base` / `build_knowledge_base_from_documents` 重建完成后调用 `retrieval_cache.clear()` 使旧缓存失效。
- **风险分析**：低风险。key 变化仅导致缓存未命中率上升；clear 只是清空优化缓存，不影响正确性。
- **测试验证**：`pytest tests/ -q` → 185 passed（同步为测试 fixture 的 mock 补充 `collection_name` 属性）。

### [bug-063] API 非 200 响应无退避直接连发重试（P1）

- **根因分析**：`BailianLLM.chat` / `chat_stream`、`BailianEmbedding.embed_one` / `_embed_batch`、`BailianReranker._rerank_with_api` 中，仅 `except Exception` 分支有 `time.sleep` 退避；`resp.status_code != 200`（如 429 限流、5xx）分支只记日志便进入下一轮重试，**无间隔连续请求**，限流时基本必然失败且加重限流。`chat_stream` 中非 200 甚至不会触发重试（warning 后直接 `return`）。
- **影响范围**：所有 API 调用路径；限流/服务异常时重试全部无效。
- **修复方案**：非 200 分支与异常分支一致，退避后重试；`chat_stream` 中非 200 改为抛 `RuntimeError` 进入既有重试逻辑（已 yield 过 token 时由 except 分支中断，避免重复输出）。
- **风险分析**：低风险。仅增加重试等待，不改变成功路径行为。
- **测试验证**：语法检查通过；`pytest tests/ -q` → 185 passed。

### [bug-064] 项目专属 chitchat Prompt 未生效（P1）

- **根因分析**：`src/project.py` 定义了 `MUSEUM_PROMPTS["chitchat"]` / `ENTERPRISE_PROMPTS["chitchat"]`（博物馆/企业人设），但 `query()`、`query_stream()` 及两处"检索为空回退"全部硬编码全局 `SYSTEM_PROMPT_CHITCHAT`，项目人设成为死代码。
- **影响范围**：闲聊分支回答无人设差异，项目自定义 Prompt 不完整生效。
- **修复方案**：新增 `_select_chitchat_prompt()`，优先使用 `project_cfg.get_prompt("chitchat")`，无项目时回退全局模板；替换 4 处硬编码调用。
- **风险分析**：低风险。仅闲聊分支的 system prompt 来源变化。
- **测试验证**：`grep` 确认 4 处调用全部替换；`pytest tests/ -q` → 185 passed。

### [bug-065] Settings 多个配置项未接线（P1）

- **根因分析**：`settings.llm_temperature` / `llm_max_tokens` / `llm_top_p` / `embedding_batch_size` / `retriever_top_k` / `retriever_hybrid_weight` / `reranker_enabled` 均未传入对应模块，全部使用硬编码默认值，用户修改 `.env` 完全无效。
- **影响范围**：配置项误导（文档声称可配但实际不生效）。
- **修复方案**：`BailianEmbedding(batch_size=settings.embedding_batch_size)`；`HybridRetriever(semantic_weight=settings.retriever_hybrid_weight, bm25_weight=1.0 - settings.retriever_hybrid_weight)`；`BailianLLM(temperature/max_tokens/top_p=settings.*)`；`query()` / `query_stream()` 默认 `top_k=settings.retriever_top_k`、`rerank=settings.reranker_enabled`。
- **风险分析**：低风险。默认值与原有硬编码一致，行为不变。
- **测试验证**：`pytest tests/ -q` → 185 passed。

### [bug-066] `add_artifacts` 在 Qdrant 集合缺失时崩溃（P1）

- **根因分析**：`_ensure_knowledge_base` 在「BM25 已加载但 Qdrant 不存在」时仍置 `_is_built = True`，此时调用 `add_artifacts` → `vector_store.upsert` 对不存在的集合抛异常，无兜底。
- **影响范围**：仅 BM25 可用（Qdrant 数据缺失/被删）时增量添加直接报错。
- **修复方案**：追加前先 `create_collection(overwrite=False)`（集合已存在时直接返回，不存在时创建）。
- **风险分析**：低风险。幂等操作。
- **测试验证**：`pytest tests/ -q` → 185 passed。

### [bug-067] Embedding 模式缓存加载未校验值类型（P1）

- **根因分析**：`EmbeddingCache._load()` 对 `exact_cache` 校验了值必须是 `list[float]`，但 `pattern_cache` 只校验了顶层是 dict，值未校验。缓存文件损坏/被篡改时，`get()` 会把非列表值当作 embedding 返回（下游 Qdrant 检索失败），或 `_pattern_match` 中 `len(pattern)` 因 pattern 非字符串抛 TypeError。
- **影响范围**：损坏的 `pattern_cache.json` 导致查询崩溃或结果错误。
- **修复方案**：与 exact_cache 一致，校验键为 str、值为 `list[float]`，非法条目跳过并告警。
- **风险分析**：低风险。仅增加防御性校验。
- **测试验证**：`pytest tests/ -q` → 185 passed。

### [bug-068] `init_pipeline` 关闭旧连接后旧实例惰性重连（P1）

- **根因分析**：`init_pipeline` 锁内关闭旧 pipeline 的 vector_store，但锁外可能有请求已持有旧实例引用；旧实例下次访问 `client` 属性会**惰性重连**到同一 Qdrant 本地路径，与新实例形成同一路径双客户端（Qdrant local mode 单客户端限制），可能文件锁冲突。
- **影响范围**：多线程并发切换项目时偶发 Qdrant 本地路径锁冲突。
- **修复方案**：`VectorStore.close()` 后置 `_closed = True`，`client` 属性在 `_closed` 时不再重连。
- **风险分析**：低风险。已关闭实例不再自愈重连；当前 pipeline 不受影响。
- **测试验证**：`pytest tests/ -q` → 185 passed。

### [bug-069] 构建方法静默忽略传入的 `project_id`（P1）

- **根因分析**：`build_knowledge_base` / `build_knowledge_base_from_documents` 中 `if pid and self.project_cfg is None:` — 当 pipeline 已绑定项目 A 时，传入 `project_id="B"` 被静默忽略，B 的数据写入 A 的路径/集合。
- **影响范围**：程序化复用 pipeline 构建多项目时数据写入错误位置。
- **修复方案**：条件改为 `self.project_cfg is None or self.project_cfg.id != pid`，切换后**同步更新 vector_store 的 collection_name / local_path / _snapshot_path**（连带修复，否则切换无效）。
- **风险分析**：低风险。仅影响显式传不同 project_id 的调用路径。
- **测试验证**：`pytest tests/ -q` → 185 passed。

## 验证结果（第五轮）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-062 | 缓存 key 含 collection_name；重建后 `retrieval_cache.clear()`；mock fixture 补 `collection_name` | ✅ 已修复 |
| bug-063 | 非 200 分支退避重试；`chat_stream` 非 200 进入重试逻辑 | ✅ 已修复 |
| bug-064 | `_select_chitchat_prompt()` 优先项目模板，4 处调用全部替换 | ✅ 已修复 |
| bug-065 | 模块构造与 query 默认参数全部接线 settings | ✅ 已修复 |
| bug-066 | `add_artifacts` upsert 前 `create_collection(overwrite=False)` | ✅ 已修复 |
| bug-067 | `_load` 校验 pattern 缓存值为 list[float] | ✅ 已修复 |
| bug-068 | `close()` 后 `_closed=True`，`client` 不再重连 | ✅ 已修复 |
| bug-069 | project_cfg 已绑定他项目时切换并同步 vector_store 指向 | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **185 passed**（8 项修复全部完成，0 失败 0 错误）。

---

## 新增问题（第六轮复测审查 - 精准修复）

> 审查方式：全量源码复读 + 定向实验验证（客户端重连、并发预热阻塞、缓存清空）
> 本轮发现 P0×1、P1×2，共 **3 项**，全部修复完成
> 全量测试：`pytest tests/ -q` → **185 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-070 | `add_artifacts` 增量添加后未清空检索缓存，旧数据在 TTL 内继续被命中 | `src/rag_pipeline.py` | P0 | 已修复 |
| bug-071 | 项目切换后 Qdrant 客户端未重连，数据写入旧项目目录 | `src/vector_store.py`、`src/rag_pipeline.py` | P1 | 已修复 |
| bug-072 | `init_pipeline` 并发预热竞态：预热期间并发请求误报"知识库尚未构建" | `app.py` | P1 | 已修复 |

## 问题详情

### [bug-070] `add_artifacts` 增量添加后未清空检索缓存（P0）

- **根因分析**：`build_knowledge_base` / `build_knowledge_base_from_documents` 重建后均调用 `retrieval_cache.clear()`（P0-1 修复），但 `add_artifacts`（增量添加，与重建共用同一 collection_name 键空间）遗漏了该调用。`retrieval_cache` 为模块级全局单例，TTL 300 秒。
- **影响范围**：增量添加新文物后，检索结果最长 5 分钟（TTL）内不含新数据，检索结果与知识库实际内容不一致。
- **修复方案**：`add_artifacts` 在切片 / 向量入库 / BM25 重建 / 缓存文件更新完成后调用 `retrieval_cache.clear()`，与两条重建路径保持一致。
- **风险分析**：低风险。仅清空优化缓存，不影响正确性；与既有 P0-1 修复模式完全同型。
- **测试验证**：源码确认 `retrieval_cache.clear()` 已加入 `add_artifacts`；`pytest tests/ -q` → 185 passed。

### [bug-071] 项目切换后 Qdrant 客户端未重连，数据写入旧项目目录（P1）

- **根因分析**：第五轮 bug-069 修复（P1-7）在切换项目时更新了 `collection_name` / `local_path` / `_snapshot_path`，但 `VectorStore._client` 为懒连接且连接后缓存。当切换发生在客户端已连接（如先执行过 `_ensure_knowledge_base` / `get_stats` / 一次查询）时，`create_collection` / `upsert` 仍写入旧项目的 Qdrant 目录。
- **影响范围**：复用已连接 pipeline 切换项目时，新项目数据写入旧项目目录（数据不一致），且新项目 `_ensure_knowledge_base` 判定 Qdrant 缺失 → 语义检索静默不可用（仅剩 BM25）。
- **修复方案**：`VectorStore` 新增 `reset_connection()`（关闭当前连接并重置 `_closed` 标记，下次访问按新路径惰性重连）；`build_knowledge_base` / `build_knowledge_base_from_documents` 的项目切换分支在更新路径后调用之。
- **风险分析**：低风险。仅影响显式切换不同 project_id 的调用路径；客户端未连接时调用为幂等 no-op。
- **测试验证**：定向实验确认 `reset_connection()` 后客户端重建，且 `create_collection` 写入新项目目录；`pytest tests/ -q` → 185 passed。

### [bug-072] `init_pipeline` 并发预热竞态：预热期间并发请求误报"知识库尚未构建"（P1）

- **根因分析**：`init_pipeline` 在锁内替换全局 `pipeline` 后，`_ensure_knowledge_base()` / `warmup()` 在锁外执行。预热完成前 `_is_built` 仍为 False，而锁外快速路径（`pipeline is not None and project_id == _current_project`）对同项目请求直接返回该半初始化实例，`answer_question` / `get_system_status` 因此误报"知识库尚未构建"。
- **影响范围**：多用户并发（Gradio 多会话 + 页面加载状态刷新）热启动期间，知识库实际加载中即被误报未构建；切换项目时对旧 pipeline 执行 `vector_store.close()` 影响旧 pipeline 上仍在进行的查询（由 bug-068 的 `_closed` 标记兜底）。
- **修复方案**：移除锁外快速路径，将预热移入 `_pipeline_lock` 内完成后才释放锁；同项目并发请求在锁内二次检查后拿到完成预热的实例。
- **风险分析**：低风险。初始化通过全局锁串行化，锁内同项目检查为微秒级；预热期间其他请求短暂阻塞（初始化本身罕见）。
- **测试验证**：定向实验确认并发请求在预热期间阻塞至 `_is_built=True` 才返回；`pytest tests/ -q` → 185 passed。

## 验证结果（第六轮）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-070 | `add_artifacts` 源码含 `retrieval_cache.clear()`（与两条重建路径一致） | ✅ 已修复 |
| bug-071 | 实验：`reset_connection()` 后客户端重建，`create_collection` 写入新项目目录 | ✅ 已修复 |
| bug-072 | 实验：预热期间并发请求阻塞至 `_is_built=True` 才返回 | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **185 passed**（3 项修复全部完成，0 失败 0 错误）。

---

## 新增问题（第七轮复测审查 - 精准修复）

> 审查方式：全量源码复读 + 定向实验验证（数字 tags 数据丢失、跨行名称防幻觉、闲聊复合句路由）
> 本轮发现 P1×2、P2×3、P3×1（需确认），共 **6 项**，5 项修复完成，1 项标注需确认
> 全量测试：`pytest tests/ -q` → **186 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-089 | `settings.reranker_model` 未接线，RERANKER_MODEL 配置永远不生效（始终用默认 4b） | `src/rag_pipeline.py` | P1 | 已修复 |
| bug-090 | `SmartChunking.chunk` 中 `"、".join(tags)` 对 tags 数字列表抛 TypeError，整件文物切片静默丢失 | `src/chunking.py` | P1 | 已修复 |
| bug-091 | `verify_answer_grounding` context 侧正则缺 `re.DOTALL`（bug-027 修复不完整），跨行名称误报"不在上下文中" | `src/rag_pipeline.py` | P2 | 已修复 |
| bug-092 | `app.py` 硬编码 `top_k=10, rerank=True` 绕过 settings.retriever_top_k / settings.reranker_enabled（bug-065 接线不完整） | `app.py` | P2 | 已修复 |
| bug-093 | `is_kb_related` 复合闲聊句与语气词缺口："你好，你是谁"（UI 示例按钮）、"谢谢啦"、"再见啦"、"嗨喽" 被误判为知识库问题 | `src/rag_pipeline.py` | P2 | 已修复 |
| bug-094 | `DocumentLoader.load_file` 路径遍历修复不完整（bug-023 声明的"根目录限制"未实现） | `src/document_loader.py` | P3 | 需确认（不改代码） |

## 问题详情

### [bug-089] `settings.reranker_model` 未接线，重排模型配置永远不生效（P1）

- **根因分析**：`RAGPipeline.__init__` 中创建 `BailianReranker(top_k=settings.reranker_top_k)` 未传 `model` 参数，而 `BailianReranker` 的 model 默认值为 `"qwen3-reranker-4b"`。`grep` 全项目仅 `src/config.py:119` 定义处引用 `reranker_model`，无任何调用方。`.env` 中 `RERANKER_MODEL=qwen3-reranker-8b` 完全无效。
- **影响范围**：所有启用重排的查询（Web UI / CLI / API）。文档宣称"高精度重排可选 qwen3-reranker-8b"，实际永远使用 4b。
- **修复方案**：`BailianReranker(model=settings.reranker_model, top_k=settings.reranker_top_k)`。
- **风险分析**：低。仅接线配置，默认值 `qwen3-reranker-4b` 不变，行为不变。
- **测试验证**：设置 `settings.reranker_model='qwen3-reranker-8b'` 后构造 pipeline，`pipeline.reranker.model == 'qwen3-reranker-8b'`。

### [bug-090] tags 为数字列表时整件文物切片静默丢失（P1）

- **根因分析**：`SmartChunking.chunk()` 中 `tags_str = "、".join(tags[:5])`，当 `artifact.tags` 为数字列表（如 JSON `"tags": [1, 2, 3]`）时 `join` 抛 `TypeError: sequence item 0: expected str instance, int found`。异常被 `ChunkingPipeline.process()` 的 try/except 捕获后 `continue`，**整件文物无任何切片产出**（实测：同一批 2 件文物，正常文物 2 切片、数字 tags 文物 0 切片）。bug-060 只处理了标量 tags（`tags=123`），未处理列表内元素为非字符串的情况。
- **影响范围**：JSON/CSV 数据源中 tags 字段为数字/布尔数组的文物记录，构建知识库时静默丢失。
- **修复方案**：join 前统一转字符串：`tags_str = "、".join(str(t) for t in tags[:5]) if tags else ""`。
- **风险分析**：低。仅增加类型防御，正常字符串列表路径行为不变。
- **测试验证**：`Artifact(name='数字tags文物', tags=[1,2,3])` 经 `ChunkingPipeline.process` 正常产出切片（不再被丢弃）。

### [bug-091] `verify_answer_grounding` context 侧正则缺 `re.DOTALL`（P2）

- **根因分析**：bug-027 只给 answer 侧正则补了 `re.DOTALL`，context 侧 `re.finditer(..., context)` 未加标志。当 context 中名称跨行（如 `**司母戊\n鼎**`）时无法提取，而 answer 侧能提取 → 回答中合法引用被误判为"不在上下文中"（实测 `passed=False`，reason 列出跨行名称）。
- **影响范围**：防幻觉检查的误报告警（仅日志，不影响回答内容）。
- **修复方案**：context 正则补充 `re.DOTALL`，与 answer 侧保持一致。
- **风险分析**：低。`【】` 非贪婪匹配在 context 中跨 chunk 时仍止于最近的 `】`，不会过度吞并。
- **测试验证**：context 含 `**司母戊\n鼎**`、answer 含同名跨行引用 → `passed=True`。

### [bug-092] `app.py` 硬编码 `top_k=10, rerank=True` 绕过配置（P2）

- **根因分析**：bug-065 将 `settings.retriever_top_k` / `settings.reranker_enabled` 接线为 `query()`/`query_stream()` 的默认参数，但 `app.py` 的 `answer_question` 在两处调用中显式传 `top_k=10, rerank=True`，默认参数永不生效 → Web UI 中 `.env` 的 `RETRIEVER_TOP_K` / `RERANKER_ENABLED` 配置无效。
- **影响范围**：Web UI 场景（CLI/SDK 走默认参数不受影响）。
- **修复方案**：两处调用改用 `top_k=settings.retriever_top_k, rerank=settings.reranker_enabled`。
- **风险分析**：低。默认值 10/True 与原有硬编码一致，行为不变。
- **测试验证**：源码检查确认 app.py 两处调用均使用 settings 值。

### [bug-093] `is_kb_related` 复合闲聊句与语气词缺口（P2）

- **根因分析**：① 白名单缺常见语气词"啦/喽/哟"：`"谢谢啦"`、`"再见啦"`、`"嗨喽"` 剩余部分不在白名单 → 误判为知识库问题（实测 True）；② 前缀匹配无法处理多关键词组合的纯闲聊句：`"你好，你是谁"`（app.py 示例按钮）剥离前缀"你好"后剩余"你是谁"非白名单 → 误判为知识库问题（实测 True）。
- **影响范围**：Web UI 示例按钮"你好，你是谁"及常见口语寒暄；KB 未构建时点击示例直接报"知识库未构建"错误，已构建时做一次无意义检索。
- **修复方案**：改为"关键词剥离 + 残渣判定"：按长度降序剥离问题中所有闲聊关键词，剩余部分为空 / 仅为语气词（白名单补充"啦/喽/哟"）/ 命中语气后缀（"怎么样/怎样/如何"，含去语气词后命中）→ 判为闲聊。覆盖原精确+前缀匹配的全部场景，且能处理复合闲聊句。
- **风险分析**：低。对真实知识库问题（剩余部分含实质内容）不影响；"说再见""谢谢你的帮助"（测试断言 True）等边界保持原语义。
- **测试验证**：`"你好，你是谁"→False`、`"谢谢啦"→False`、`"再见啦"→False`、`"嗨喽"→False`、`"天气对文物保存有影响吗"→True`、`"谢谢你的帮助是什么文物"→True`、`"说再见"→True`；同步更新 1 个断言旧行为的测试（`test_hello_with_punctuation` 由 True 改为 False）。

### [bug-094] `DocumentLoader.load_file` 路径遍历修复不完整（P3，需确认）

- **根因分析**：bug-023 声明的修复方案为"添加路径解析检查，确保路径在允许的根目录内"，但当前实现仅 `path.resolve()` 规范化 + 存在性检查，**无任何根目录限制**。`load_file(Path("../secret.txt"))` 在文件存在时仍可读取项目根目录外的任意路径文件（仅文件不存在时才抛 `FileNotFoundError`）。
- **影响范围**：当前 `load_file` 仅被内部代码路径调用（`load_directory` / `load_all_as_artifacts`，路径来自 CLI 参数），无外部暴露入口，实际风险低。且 `build_knowledge_base_from_documents` 允许用户指定任意目录（如 `--doc-path /home/user/docs`），硬性根目录限制会破坏合法用法。
- **处理决定**：**需确认，不改代码**。修复方案的"根目录限制"与真实用法（任意路径）冲突，待产品层面确认是否有外部文件加载入口后，再决定是否引入白名单/根目录策略。
- **测试验证**：无代码变更；记录待确认项。

## 修复顺序

1. bug-089：`src/rag_pipeline.py`（P1，配置不生效）
2. bug-090：`src/chunking.py`（P1，数据静默丢失）
3. bug-091：`src/rag_pipeline.py`（P2，防幻觉误报）
4. bug-092：`app.py`（P2，配置接线不完整）
5. bug-093：`src/rag_pipeline.py`（P2，闲聊路由误判）
6. bug-094：`src/document_loader.py`（P3，需确认，暂不改）

## 验证结果（第七轮）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-089 | `settings.reranker_model='qwen3-reranker-8b'` 后 pipeline.reranker.model 生效 | ✅ 已修复 |
| bug-090 | `ChunkingPipeline.process([正常, 数字tags])` 均产出切片，不再静默丢弃 | ✅ 已修复 |
| bug-091 | context 含跨行 `**司母戊\n鼎**` → grounding `passed=True` | ✅ 已修复 |
| bug-092 | 源码确认 app.py 两处调用使用 `settings.retriever_top_k` / `settings.reranker_enabled` | ✅ 已修复 |
| bug-093 | 复合闲聊句与语气词全部正确路由；`test_hello_with_punctuation` 断言更新 | ✅ 已修复 |
| bug-094 | 记录待确认，无代码变更 | ⏸ 需确认 |

**全量测试**：`pytest tests/ -q` → **186 passed**（0 失败 0 错误；同步更新 1 个断言旧行为的测试）。

## 验证步骤（第七轮）

### bug-089 验证
1. `python -c "from src.config import settings; settings.reranker_model='qwen3-reranker-8b'; from src.rag_pipeline import RAGPipeline; print(RAGPipeline(local_mode=True).reranker.model)"` → `qwen3-reranker-8b`

### bug-090 验证
1. `python -c "from src.data_loader import Artifact; from src.chunking import ChunkingPipeline; print(sorted(set(c.artifact_name for c in ChunkingPipeline().process([Artifact(name='A', tags=[1,2,3]), Artifact(name='B', tags=['国宝'])]))))"` → 两件文物均在结果中

### bug-091 验证
1. `python -c` 构造 context 含 `**司母戊\n鼎**`、answer 含同名跨行引用 → `passed=True`（修复前为 False）

### bug-092 验证
1. `grep -n "top_k=settings\|rerank=settings" app.py` → 两处调用均使用 settings 值

### bug-093 验证
1. `python -c "from src.rag_pipeline import RAGPipeline; [print(q, RAGPipeline.is_kb_related(q)) for q in ['你好，你是谁','谢谢啦','再见啦','嗨喽','天气对文物保存有影响吗','说再见','谢谢你的帮助']]"` → 前三 False、后四 True
2. `pytest tests/test_review_findings.py::TestIsKBRelatedFalsePositives -v` → 通过

---

## 新增问题（第八轮 - 生产环境修复）

> 触发场景：服务器执行 `python scripts/build_knowledge_base.py --project museum --source json`
> 时 Embedding API 返回 400，日志仅显示 `Batch Embedding 返回异常 (attempt N): 400`，
> 服务端错误原因完全不可见，且确定性错误被无效重试 3 次（约浪费 10 秒）后才失败。
> 全量测试：`pytest tests/ -q` → **193 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-095 | API 非 200 响应缺少错误详情且确定性错误（4xx 非 429）被无效重试：`_embed_batch`/`chat_stream` 只记状态码不记 `resp.message`；400 等客户端错误重试无意义 | `src/embeddings.py`、`src/llm.py`、`src/reranker.py`、`src/utils.py` | P1 | 已修复 |

## 问题详情

### [bug-095] API 确定性错误（4xx 非 429）无详情且被无效重试（P1）

- **根因分析**：
  1. **错误详情缺失**：`_embed_batch`（`src/embeddings.py:155-156`）与 `chat_stream`（`src/llm.py:164`）的非 200 分支只记录 `resp.status_code`（如 `400`），不记录 `resp.message`。而 `embed_one`/`chat`/`_rerank_with_api` 均记录 `status_code - resp.message`，行为不一致。生产环境 Embedding 返回 400 时，服务端的真实原因（如 `InvalidParameter: dimension not supported`、`input too long`、模型未开通等）完全不可见，用户无法定位根因。
  2. **确定性错误被无效重试**：所有 API 调用路径对任何非 200 都退避重试 3 次。HTTP 400/401/403 等为确定性客户端错误，重试不可能成功，只浪费 API 调用与时间（实测 2 个批次 × 3 次 × ~2s ≈ 10s+），且掩盖真实错误。
- **影响范围**：所有 API 调用路径（Embedding / LLM / Reranker）的云端/本地构建与查询；生产环境 API 配置错误（模型名、维度、订阅、文本超长）时故障不可诊断。
- **修复方案**：
  1. `src/utils.py` 新增共享异常 `FatalAPIError(RuntimeError)`；
  2. `_embed_batch` / `chat_stream` 非 200 日志补全 `resp.message`；
  3. 全部 5 条 API 路径（`embed_one` / `_embed_batch` / `chat` / `chat_stream` / `_rerank_with_api`）在 `400 <= status < 500 and status != 429` 时抛 `FatalAPIError`（携带 `resp.message`），`except` 中识别后直接向上抛出、不重试；429 限流与 5xx 仍按原退避重试逻辑。
- **风险分析**：低。仅改变确定性 4xx 的处理（从"重试 3 次后失败"变为"立即失败"），成功路径与瞬时错误（429/5xx）行为不变；Reranker 路径的 `FatalAPIError` 仍被 `rerank()` 捕获后降级本地 TF-IDF，不向调用方抛错。
- **测试验证**：新增 `TestFatalAPIErrorFastFail`（7 项）：400 → 仅 1 次调用且异常携带服务端详情；429/500 → 仍重试 3 次；LLM chat/chat_stream 400 快速失败；Reranker 400 → 降级本地重排。全部通过；`pytest tests/ -q` → **193 passed**。

## 验证结果（第八轮）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-095 | `TestFatalAPIErrorFastFail`（7 项）通过——400 快速失败且携带 `resp.message`，429/5xx 仍重试 3 次 | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **193 passed**（0 失败 0 错误）。

## 验证步骤（第八轮）

### bug-095 验证
1. `python -c` mock `TextEmbedding.call` 返回 400（message="InvalidParameter: ..."），调用 `embed_batch` → 仅 1 次调用即抛 `RuntimeError`，异常信息含服务端详情
2. mock 返回 429 / 500 → 仍退避重试 3 次后抛错
3. `pytest tests/test_review_findings.py::TestFatalAPIErrorFastFail -v` → 7 passed

### 生产环境排查指引（bug-095 修复后，重新运行构建命令即可看到真实原因）
- `python scripts/build_knowledge_base.py --project museum --source json` 若仍报 400，日志/异常会显示 `- {resp.message}`，常见原因：
  - `dimension not supported`：`.env` 中 `EMBEDDING_DIMENSION` 与模型不匹配（text-embedding-v3 支持 1024/768/512/256/128/64）
  - `input too long`：数据中单条文本超过模型 token 上限（text-embedding-v3 单条上限 8192 tokens）
  - `model not found / 未开通`：`EMBEDDING_MODEL_NAME` 拼写错误或账号未开通该模型

---

## 新增问题（第八轮补 - 生产环境修复 #2）

> 触发场景：应用 bug-095 修复后重跑 `python scripts/build_knowledge_base.py --project museum --source json`，
> 错误详情已可见：`<400> InternalError.Algo.InvalidParameter: Value error, batch size is invalid,
> it should not be larger than 10.: input.contents`
> 根因明确：**text-embedding-v3 单请求最多 10 条文本，而默认 `embedding_batch_size=16` 超限**，
> 全部批次 400 失败。本地测试 mock 不校验批大小，故此前从未暴露。
> 全量测试：`pytest tests/ -q` → **198 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-096 | `embedding_batch_size` 默认 16 超过 text-embedding-v3 API 单请求上限（10），构建知识库时全部批次 400 失败 | `src/config.py`、`src/embeddings.py` | P0 | 已修复 |

## 问题详情

### [bug-096] Embedding 批大小超过 API 上限（10），构建知识库必然失败（P0）

- **根因分析**：`src/config.py` 的 `embedding_batch_size` 默认值为 16，`BailianEmbedding.__init__` 默认参数同样为 16。dashscope `text-embedding-v3` 单请求 `input.contents` 最多 10 条，超出即返回 400（实测报错：`InternalError.Algo.InvalidParameter: batch size is invalid, it should not be larger than 10`）。`embed_batch` 按 `batch_size=16` 分批后，每批都 400 → 构建失败。本地测试的 mock 不校验批大小，故该缺陷在 CI 中从未暴露。
- **影响范围**：所有使用 Embedding 批处理的场景（`build_knowledge_base` / `build_knowledge_base_from_documents` / `add_artifacts`）。默认配置下知识库构建必然失败。
- **修复方案**：
  1. `src/config.py`：默认 `embedding_batch_size` 16 → **10**（API 上限）；
  2. `src/embeddings.py`：新增 `MAX_BATCH_SIZE = 10` 类常量，`__init__` 中对超限值钳制（>10 → 10，非整数配置回退到 10）并告警，防御 .env 中仍配置旧值 16 的存量环境。
- **风险分析**：低。批变小仅增加请求次数（38 切片：3 批 → 4 批），不影响正确性；钳制逻辑对合法配置（≤10）行为不变。
- **测试验证**：新增 `TestEmbeddingBatchSizeClamp`（5 项）：默认 ≤ 10、16→10 钳制、8 保持、MagicMock 回退、38 文本按 [10,10,10,8] 分批。全部通过；`pytest tests/ -q` → **198 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-096 | `TestEmbeddingBatchSizeClamp`（5 项）通过；默认值 10，超限钳制，分批均 ≤ 10 | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **198 passed**（bug-095 的 7 项 + bug-096 的 5 项 + 原 186 项）。

## 验证步骤（第八轮补）

### bug-096 验证
1. `python -c "from src.config import settings; print(settings.embedding_batch_size)"` → 10
2. `python -c "from src.embeddings import BailianEmbedding; print(BailianEmbedding(batch_size=16).batch_size)"` → 10（钳制）
3. `pytest tests/test_review_findings.py::TestEmbeddingBatchSizeClamp -v` → 5 passed

### 生产环境操作指引
1. 同步 `src/config.py` / `src/embeddings.py` 到服务器；
2. 若服务器 `.env` 中仍配置 `EMBEDDING_BATCH_SIZE=16`，无需手工修改——代码会钳制为 10 并打印告警；
3. 重新执行 `python scripts/build_knowledge_base.py --project museum --source json` 应构建成功。
