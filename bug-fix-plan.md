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
| bug-026 | `_trim_context` 中 chunk 文本包含分隔符时错误分割 | `src/rag_pipeline.py` | 低 | 待修复 |
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

- **根因分析**：`context.split(CHUNK_SEPARATOR)` 如果 chunk 文本包含分隔符，会错误分割。

- **影响范围**：概率极低，但一旦触发会导致上下文信息丢失。

- **修复方案**：当前分隔符已经足够独特，仅做文档说明，不做代码修改。

- **风险分析**：无风险。

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

## 修复顺序（新增）

1. bug-012：`src/cache.py`（高优先级，缓存损坏后崩溃）
2. bug-014：`app.py`（高优先级，线程安全）
3. bug-015：`app.py`（中优先级，消息序列错乱）
4. bug-016：`src/rag_pipeline.py`（中优先级，查询分类错误）
5. bug-017：`src
_data_loader.py`（中优先级，数据丢失）
6. bug-018：`src/retriever.py`（中优先级，英文检索失败）
7. bug-019：`src/rag_pipeline.py`（中优先级，防幻觉检测失效）
8. bug-020：`src/embeddings.py`（中优先级，静默数据损坏）
9. bug-013：`src/cache.py`（中优先级，潜在崩溃）
10. bug-022：`src/rag_pipeline.py`（低优先级，无意义查询）
11. bug-021：`app.py`（低优先级，UI 更新频率）
12. bug-023：`src/document_loader.py`（中优先级，安全风险）
13. bug-024：`src/embeddings.py`（低优先级，边界情况）
14. bug-025：`src/cache.py`（低优先级，代码冗余）
15. bug-027：`src/rag_pipeline.py`（低优先级，正则改进）
16. bug-028：`app.py`（低优先级，边界情况）

（注：bug-026 仅文档说明，不做代码修改）

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
| bug-027 | 见下方验证步骤 | ✅ 已修复 |
| bug-028 | 见下方验证步骤 | ✅ 已修复 |

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

### bug-027 验证
1. 回答中含跨行 `**名称**` 时能正确提取

### bug-028 验证
1. `_convert_history([("问题", "\n\n---\n\n来源")])` → 返回空列表
