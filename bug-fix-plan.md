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

---

## 新增问题（第八轮补 - 生产环境修复 #3）

> 触发场景：服务器 `python scripts/run_qa.py -q "推荐一些代表性的文物" --project museum`
> 日志出现防幻觉告警：`回答中提到了以下不在上下文中的内容: ['推荐理由', '参观建议', '地域', '简介', '材质', '清明上河图（北宋张择端本）', '朝代']`
> 经核实为**误报**（bug-046 的 verify_answer_grounding 仅记录日志、不拒绝回答，不影响功能），
> 但误报率高会刷屏日志并掩盖真实幻觉风险。
> 全量测试：`pytest tests/ -q` → **201 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-097 | `verify_answer_grounding` 防幻觉误报：① LLM 回答中的结构化字段标签（`**推荐理由**` 等）被当作文物名称；② 名称变体（`清明上河图（北宋张择端本）` vs 上下文 `清明上河图`）精确比较不匹配 | `src/rag_pipeline.py` | P2 | 已修复 |

## 问题详情

### [bug-097] 防幻觉检查误报：字段标签与名称变体（P2）

- **根因分析**：
  1. **字段标签被当名称**：`verify_answer_grounding` 从回答中提取所有 `**...**` 内容作为"名称"。LLM 结构化回答大量使用加粗做字段标签（`**推荐理由**`、`**材质**`、`**简介**`、`**参观建议**`、`**地域**`、`**朝代**`），上下文（`_build_context` 生成的 `【{artifact_name}】`）中不存在这些词 → 大面积误报；
  2. **名称变体不匹配**：上下文名称为 `【清明上河图】`，回答中 LLM 可能补充描述写成 `**清明上河图（北宋张择端本）**`，精确 `n not in context_names` 判为不在上下文。
- **影响范围**：所有走 RAG 的查询的防幻觉日志；误报只产生日志噪音（不拒绝回答），但会掩盖真实幻觉风险、干扰排查。
- **修复方案**：
  1. 新增 `FIELD_LABELS` 字段标签黑名单（推荐理由/材质/简介/朝代/参观建议/历史意义/文化价值/类别/现藏/出土地 等 30+ 常见字段词），提取后直接排除；
  2. 名称匹配改为**变体匹配**：回答名包含上下文名（或反向包含）即视为命中（如 `清明上河图（北宋张择端本）` ⊇ `清明上河图`）。
- **风险分析**：低。变体匹配略微放宽（上下文名是回答名子串、或反之），真实幻觉（名称与上下文无任何包含关系，如 `越王勾践剑` vs `司母戊鼎`）仍能检出；现有 3 项防幻觉测试语义保持不变。
- **测试验证**：新增 `TestAnswerGroundingFalsePositives`（3 项）：字段标签不误报、名称变体不误报、真实幻觉仍检出。全部通过；`pytest tests/ -q` → **201 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-097 | 生产日志场景（字段标签+名称变体）→ `passed=True`；真实幻觉 `越王勾践剑` → 仍检出；`TestAnswerGroundingFalsePositives`（3 项）通过 | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **201 passed**（原 186 + bug-095 的 7 + bug-096 的 5 + bug-097 的 3）。

## 验证步骤（第八轮补）

### bug-097 验证
1. `python -c` 构造 context `【清明上河图】`、answer `**清明上河图（北宋张择端本）**是名画` → `passed=True`
2. `python -c` 构造 answer 含 `**推荐理由**`/`**材质**` 字段标签 → `passed=True`
3. `python -c` 构造 answer 含 `**越王勾践剑**`（上下文无）→ `passed=False` 且 `missing` 含该名
4. `pytest tests/test_review_findings.py::TestAnswerGroundingFalsePositives -v` → 3 passed

### 说明（同轮日志中的 Reranker 400）
日志中 `Qwen3-Reranker API 异常: 400 - Model not exist` 为**环境问题**（账号未开通 qwen3-reranker-4b），
非代码缺陷：bug-095 的 fail-fast 已生效（仅 attempt 1 即降级，未重试 3 次），
自动降级本地 TF-IDF 重排，功能不受影响。如需消除：开通该模型，或 `.env` 改 `RERANKER_MODEL`
为已开通模型 / `RERANKER_ENABLED=false`。

---

## 新增问题（第八轮补 - 生产环境修复 #4）

> 触发场景：服务器 `python app.py --project museum --host 0.0.0.0 --port 7860` 启动 Web UI 失败：
> `TypeError: Chatbot.__init__() got an unexpected keyword argument 'show_copy_button'`
> 且日志出现 `UserWarning: The parameters have been moved from the Blocks constructor to
> the launch() method in Gradio 6.0: theme, css`
> 根因：**服务器 Gradio 为 6.x**（本地同为 6.22.0），`show_copy_button`/`bubble_full_width`
> 参数已被移除，`Blocks` 构造器的 `theme`/`css` 也移至 `launch()`。本地测试未覆盖 create_ui，故未暴露。
> 全量测试：`pytest tests/ -q` → **203 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-098 | Gradio 6.0 破坏性变更导致 Web UI 无法启动：`Chatbot(show_copy_button=...)` 直接 TypeError；`Blocks(theme/css=...)` 参数已移至 launch() | `app.py` | P0 | 已修复 |

## 问题详情

### [bug-098] Gradio 6.0 破坏性变更导致 Web UI 无法启动（P0）

- **根因分析**：服务器安装 Gradio 6.x（`requirements.txt` 约束为 `>=4.44.0`，6.0 属于允许范围）。Gradio 6.0 的破坏性变更：
  1. `gr.Chatbot` 移除 `show_copy_button` / `bubble_full_width` 参数（改用 `buttons=["copy"]` / `layout="bubble"`），代码传 `show_copy_button=True` 直接 `TypeError` 崩溃；
  2. `gr.Blocks` 构造器的 `theme` / `css` 参数移除，改由 `launch(theme=..., css=...)` 传入（旧写法仅告警不崩溃，但样式不生效）。
- **影响范围**：所有 Gradio 6.x 环境启动 Web UI 均失败（本地 6.22.0 同款问题，此前未运行 create_ui 故未暴露）。
- **修复方案**：按 `gr.__version__` 主版本分支：
  - `_GRADIO_MAJOR >= 6`：`Chatbot(buttons=["copy"], layout="bubble")`，`theme/css` 移到 `demo.launch()`；
  - `_GRADIO_MAJOR < 6`：保持原 `show_copy_button=True / bubble_full_width=False`，`theme/css` 留在 `Blocks()`。
  - theme/css 提取为模块级常量 `_UI_THEME` / `_UI_CSS` 供两处复用。
- **风险分析**：低。仅 UI 参数按版本分支，交互逻辑不变；4.x/5.x/6.x 均可运行。
- **测试验证**：新增 `TestGradio6Compatibility`（2 项）：当前 Gradio 版本下 `create_ui` 成功构建不抛 TypeError；`Chatbot` 参数在当前版本签名中合法。全部通过；`pytest tests/ -q` → **203 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-098 | Gradio 6.22 下 `create_ui` 成功构建；mock launch 确认 theme/css 正确传入；`TestGradio6Compatibility`（2 项）通过 | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **203 passed**（原 186 + bug-095~097 的 15 + bug-098 的 2）。

## 验证步骤（第八轮补）

### bug-098 验证
1. `python -c "import app; demo = app.create_ui()"` → 不抛 TypeError（Gradio 6.x 下）
2. mock `Blocks.launch` 后运行 `app.main()` → launch 收到 theme/css 参数（6.x）
3. `pytest tests/test_review_findings.py::TestGradio6Compatibility -v` → 2 passed

### 生产环境操作指引
1. 同步 `app.py` 到服务器 `/data/codes/rag_chat/`；
2. 重新执行 `python app.py --project museum --host 0.0.0.0 --port 7860`，Web UI 正常启动；
3. （可选）如需继续使用 Gradio 6.x 的样式，无需改动——theme/css 已由 launch() 传入。

---

## 新增问题（第八轮补 - 生产环境修复 #5）

> 触发场景：服务器 `python app.py --project museum --host 0.0.0.0 --port 7860` 启动成功，
> 但访问 `http://10.0.2.200:7860` 白屏，日志报：
> `TypeError: GZipResponder.__init__() missing 1 required keyword-only argument: 'thread_minimum_size'`
> 根因：**Gradio 6.x 与服务器上旧版 Starlette（0.x）不兼容**——
> gradio 6.22 的 `brotli_middleware.py` 按 `GZipResponder(app, minimum_size)` 位置参数调用，
> 而旧版 Starlette 的 `GZipResponder` 要求必填 keyword-only 参数 `thread_minimum_size`。
> 本地环境（gradio 6.22.0 + starlette 1.3.1 + fastapi 0.141.1）为官方匹配组合，可正常启动。

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-099 | Gradio 6.x 与旧版 Starlette（0.x）不兼容：`GZipResponder` 必填 `thread_minimum_size`，ASGI 请求崩溃 → 页面白屏 | `requirements.txt`（版本约束加固，环境问题） | P0 | 已修复（版本对齐） |

## 问题详情

### [bug-099] Gradio 6.x 与 Starlette 版本不兼容导致页面白屏（P0）

- **根因分析**：Gradio 6.22.0 官方依赖约束为 `starlette>=1.0.1,<2.0`、`fastapi>=0.115.2,<1.0`（已通过 `importlib.metadata.requires('gradio')` 核实）。服务器环境中 Starlette 为 0.x（该版本 `GZipResponder.__init__` 签名含必填 keyword-only 参数 `thread_minimum_size`），gradio 6.22 的 `brotli_middleware.py:88` 调用 `GZipResponder(self.app, self.minimum_size)` 时缺失该参数 → `TypeError`（ASGI 中间件初始化崩溃，发生在每次请求前，页面无内容）。本地 starlette 1.3.1 签名 `(self, app, minimum_size, compresslevel=9)` 与 gradio 调用方式匹配，本地正常。
- **影响范围**：所有 Gradio 6.x + Starlette 0.x 组合的环境；Web UI 完全不可用（白屏）。
- **修复方案**：
  1. **服务器（立即）**：将 Starlette/FastAPI 升级到与 gradio 6.x 匹配的版本（推荐与本地一致组合）：
     `pip install "starlette>=1.0.1,<2.0" "fastapi>=0.115.2,<1.0"`（或 `pip install -U starlette fastapi` 让 pip 解析）；
  2. **requirements.txt（加固）**：显式声明 `starlette>=1.0.1,<2.0` 与 `fastapi>=0.115.2,<1.0` 并附注释，防止新环境装到不兼容组合。
- **风险分析**：低。starlette 1.x 为 gradio 6.x 官方依赖范围；升级 fastapi/starlette 后需重启服务验证（fastapi 0.115.2+ 与 starlette 1.x 配套）。
- **测试验证**：本地（gradio 6.22.0 + starlette 1.3.1）验证 `GZipResponder(app, minimum_size)` 位置调用成功、`create_ui` 构建成功；`pytest tests/ -q` → **203 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-099 | 本地 starlette 1.3.1 下 `GZipResponder(app, minimum_size)` 调用成功；requirements.txt 已加匹配约束 | ✅ 已修复（版本对齐） |

**全量测试**：`pytest tests/ -q` → **203 passed**（无代码逻辑改动，仅环境版本约束加固）。

## 验证步骤（第八轮补）

### 服务器操作
```bash
# ① 升级到与 Gradio 6.x 匹配的版本（推荐与本地一致组合）
pip install "starlette>=1.0.1,<2.0" "fastapi>=0.115.2,<1.0"

# ② 确认版本
python -c "import starlette, fastapi, gradio; print(starlette.__version__, fastapi.__version__, gradio.__version__)"

# ③ 重启 Web UI
python app.py --project museum --host 0.0.0.0 --port 7860

# ④ 访问 http://10.0.2.200:7860 应正常显示页面
```

---

## 新增问题（第八轮补 - 生产环境修复 #6）

> 触发场景：按 bug-099 指引升级 starlette 后（用户执行 `pip install "starlette>=1.0.1,<2.0"`，
> 实际装到 **1.4.0**），页面仍白屏，报错与升级前完全相同：
> `TypeError: GZipResponder.__init__() missing 1 required keyword-only argument: 'thread_minimum_size'`
> 根因：**starlette 1.4.0 的 GZipResponder 签名再次变更**——新增必填 keyword-only 参数
> `thread_minimum_size`（1.3.1 无此参数）。gradio 6.22.0（PyPI 最新版）的
> `brotli_middleware.py:88` 仍按 `GZipResponder(app, minimum_size)` 两个位置参数调用，
> 与 1.4.0 不兼容。即：starlette 1.3.1 ↔ gradio 6.22 兼容；starlette 1.4.0 ↔ gradio 6.22 不兼容。

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-100 | starlette 1.4.0 的 `GZipResponder` 新增必填 keyword-only `thread_minimum_size`，与 gradio 6.22（PyPI 最新）不兼容，ASGI 请求崩溃 → 页面白屏 | `requirements.txt`（版本约束收紧） | P0 | 已修复（版本对齐） |

## 问题详情

### [bug-100] starlette 1.4.0 与 gradio 6.22 不兼容（GZipResponder 签名变更）（P0）

- **根因分析**：下载 starlette 1.4.0 wheel 源码核实：
  ```python
  # starlette 1.4.0  middleware/gzip.py
  class GZipResponder(IdentityResponder):
      def __init__(self, app, minimum_size, compresslevel=9, *, thread_minimum_size: int):
          ...
  ```
  `thread_minimum_size` 为**必填 keyword-only**（无默认值）。starlette 1.3.1 签名 `(self, app, minimum_size, compresslevel=9)` 无此参数。gradio 6.22.0（PyPI 最新版，无更新修复）的 `brotli_middleware.py:88` 调用 `GZipResponder(self.app, self.minimum_size)` 只传两个位置参数 → 1.4.0 下缺 `thread_minimum_size` → TypeError。上一轮 bug-099 的约束 `starlette>=1.0.1,<2.0` 范围过宽，允许装到 1.4.0。
- **影响范围**：所有 gradio 6.22 + starlette 1.4.x 组合的环境；Web UI 白屏不可用。
- **修复方案**：
  1. **服务器（立即）**：降级 starlette 到已验证兼容的 1.3.1（与本地一致，不动 fastapi 0.141.1）：
     `pip install "starlette==1.3.1"`，重启服务；
  2. **requirements.txt（收紧）**：`starlette>=1.0.1,<1.4`（排除 1.4.0 破坏性版本），注释说明原因。
- **风险分析**：低。1.3.1 为本地验证过的兼容版本；fastapi 0.141.1 的 starlette 约束（<2.0）满足 1.3.1。
- **测试验证**：本地 starlette 1.3.1 下 `GZipResponder(app, minimum_size)` 调用成功（已实测）；`pytest tests/ -q` → **203 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-100 | starlette 1.4.0 源码确认 `thread_minimum_size` 必填；1.3.1 签名兼容；requirements 收紧为 `>=1.0.1,<1.4` | ✅ 已修复（版本对齐） |

**全量测试**：`pytest tests/ -q` → **203 passed**（无代码逻辑改动，仅环境版本约束收紧）。

## 验证步骤（第八轮补）

### 服务器操作
```bash
# ① 降级 starlette 到兼容版本（本地已验证：1.3.1）
pip install "starlette==1.3.1"

# ② 确认版本（fastapi 不动，仍为 0.141.1）
python -c "import starlette, fastapi, gradio; print(starlette.__version__, fastapi.__version__, gradio.__version__)"
# 期望输出: 1.3.1 0.141.1 6.22.0

# ③ 重启 Web UI
python app.py --project museum --host 0.0.0.0 --port 7860

# ④ 访问 http://10.0.2.200:7860 应正常显示页面
```

---

## 新增问题（第八轮补 - 生产环境修复 #7）

> 触发场景：Web UI 提问"有什么文物"，页面直接返回"错误"无答案。服务器日志：
> `gradio.exceptions.Error: Data incompatible with messages format. Each message should be a dictionary with 'role' and 'content' keys or a ChatMessage object.`
> 根因：**Gradio 6.0 的 Chatbot 消息格式变更**——从 `[(user, assistant), ...]` 元组列表
> 改为 `[{"role": ..., "content": ...}, ...]` dict 列表。bug-098 只修了 Chatbot 构造参数
> （show_copy_button 等），未适配数据格式，`answer_question` 仍产出 tuple 历史 → postprocess 校验失败。
> 全量测试：`pytest tests/ -q` → **208 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-101 | Gradio 6.0 Chatbot 消息格式变更（dict 列表）未适配：`answer_question` 产出 tuple 历史，postprocess 校验失败，页面返回"错误" | `app.py` | P0 | 已修复 |

## 问题详情

### [bug-101] Gradio 6.0 Chatbot 消息格式变更导致问答页面报错（P0）

- **根因分析**：Gradio 6.0 起 `gr.Chatbot` 的 value 格式从 4/5.x 的 `List[Tuple[str, str]]`（`[(user, assistant), ...]`）改为 `List[Dict]`（`[{"role": "user", "content": ...}, {"role": "assistant", "content": ...}]`）。`app.py` 的 `answer_question` 仍按 tuple 格式追加/更新历史（`history.append((q, ""))`、`history[-1] = (q, display)`），6.x 的 `Chatbot.postprocess` 校验消息必须含 `role`/`content` 键 → 抛 `Error`，页面显示"错误"。bug-098 仅修复了 Chatbot 构造参数（`show_copy_button` → `buttons`），未适配数据格式。
- **影响范围**：所有 Gradio 6.x 环境的 Web UI 问答（发送任何问题均报错）。
- **修复方案**：
  1. 新增 `_iter_history_pairs(history)`：按元素类型**自动检测**消息格式（dict → 6.x 交替解析；tuple → 4/5.x 成对解析），统一归一化为 `(user_msg, assistant_msg)` 对，`_convert_history` 复用原有逻辑；
  2. 新增 `_append_conversation(history, user, assistant)` / `_update_last_assistant(history, user, assistant)`：按 Gradio 主版本产出 dict（6.x）或 tuple（4/5.x）消息；
  3. `answer_question` 全部 9 处 history 追加/更新操作改用上述 helper。
- **风险分析**：低。自动检测格式不依赖版本号，4/5/6.x 均可运行；现有 tuple 格式测试不受影响。
- **测试验证**：新增 `TestGradio6ChatMessageFormat`（5 项）：dict/tuple 两种格式归一化、`_convert_history` dict 输入、`_append_conversation`/`_update_last_assistant` 产出合法 dict、完整 `answer_question` 流程产出合法 dict 历史。全部通过；`pytest tests/ -q` → **208 passed**。同步更新 2 个断言 tuple 格式的旧测试（`TestAnswerQuestionEmpty` 改用 `_iter_history_pairs` 解析）。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-101 | `TestGradio6ChatMessageFormat`（5 项）通过；完整问答流程产出合法 dict 消息；208 passed | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **208 passed**（原 203 + bug-101 的 5 - 0 失败）。

## 验证步骤（第八轮补）

### bug-101 验证
1. `python -c` 调 `_iter_history_pairs`：dict 与 tuple 两种 history 均归一化为 `(user, assistant)` 对
2. `python -c` mock pipeline 后跑 `answer_question("有什么文物", [], use_stream=False)`：最终 history 为合法 dict 消息列表（含 role/content）
3. `pytest tests/test_review_findings.py::TestGradio6ChatMessageFormat -v` → 5 passed

### 生产环境操作指引
1. 同步 `app.py` 到服务器；
2. 重启 `python app.py --project museum --host 0.0.0.0 --port 7860`；
3. 页面提问"有什么文物"应正常返回答案（含检索来源展示）。

---

## 新增问题（第八轮补 - 生产环境修复 #8）

> 触发场景：Web UI 提问"有什么文物"，回答可正常生成，但内容**循环重复至少 10 次**
> （每次循环为完整"5件推荐 + 结尾注"，注内容逐次演化："可随时告知，"→"我将为您进一步查询整理"）。
> 本地 mock 验证：app.py 流式聚合逻辑正常（单次 token 流不会产生循环），排除代码拼接 bug。
> 根因：**LLM 单次生成中的递归重复（degeneration loop）**——推荐类回答在结尾注
> （"可随时告知"）引导下，自行追加了与开头相同的引导句"以下是5件极具代表性的…推荐"并再次推荐，
> 形成自我递归。长生成 + 高 max_tokens / temperature 时更易触发。
> 全量测试：`pytest tests/ -q` → **208 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-102 | LLM 推荐类回答递归重复（generation loop）：结尾注引导模型再次推荐相同内容，循环 10+ 次 | `src/rag_pipeline.py`、`src/project.py`（prompt 防重复指令） | P1 | 已修复（prompt 缓解 + 诊断指引） |

## 问题详情

### [bug-102] LLM 推荐类回答递归重复（P1）

- **根因分析**：用户提问"有什么文物"（recommendation 类），LLM 生成完整推荐后，结尾注"可随时告知，…"促使模型追加与开头相同的引导句"以下是5件极具代表性的中国国宝级文物推荐"并再次推荐 → 自我递归循环（实测 10 次+）。特征：每次循环为完整推荐段、结尾注逐次演化（"可随时告知，"→"我将为您进一步查询整理"），为 LLM 生成而非字符串拼接。本地 mock 验证 app.py 流式聚合无重复，排除代码 bug。长生成长度（max_tokens 大）与较高 temperature 会放大该现象。
- **影响范围**：推荐类问题（"有什么/有哪些/推荐…"）在 Web UI / CLI / API 的回答质量；回答内容冗余 10 倍，浪费 tokens 与显示空间。
- **修复方案**：
  1. **prompt 防重复（代码）**：默认、museum、enterprise 三个 recommend 模板的"输出格式要求"增加："回答必须一次完成：列出全部推荐项后直接结束，不要重复推荐、不要追加与前面相同的推荐列表，不要在结尾再次生成新的推荐内容"；
  2. **诊断指引（环境）**：检查 `LLM_MAX_TOKENS`（建议 2048~4096，过大给循环留空间）与 `LLM_TEMPERATURE`（建议 0.7，勿调高）；用非流式直接调用验证原始输出是否循环，区分"LLM 生成问题"与"流式处理问题"。
- **风险分析**：低。prompt 指令不影响正常回答结构；`{context}` 占位符保留。
- **测试验证**：208 passed；3 个 recommend prompt 均含防重复指令且 `{context}` 占位符保留。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-102 | 本地 mock 流式聚合无重复（排除代码 bug）；3 个 recommend prompt 已加防重复指令；208 passed | ✅ 已修复（prompt 缓解） |

**全量测试**：`pytest tests/ -q` → **208 passed**。

## 验证步骤（第八轮补）

### 服务器诊断命令
```bash
# ① 检查生成参数（max_tokens 过大给循环留空间，temperature 过高放大重复）
python -c "from src.config import settings; print('max_tokens:', settings.llm_max_tokens, '| temperature:', settings.llm_temperature)"

# ② 非流式直接调用 LLM，验证原始输出是否循环（区分 LLM 生成问题 vs 流式处理问题）
python -c "
from src.llm import BailianLLM
ans = BailianLLM().chat([{'role':'user','content':'有什么文物'}], system_prompt='请推荐3个文物，列出后直接结束')
print('回答长度:', len(ans), '| 司母戊鼎出现次数:', ans.count('司母戊鼎'))
"
# 若出现次数 > 1 → LLM 生成循环（prompt 已缓解）；若 == 1 而流式页面循环 → 另查流式链路

### 建议 .env 调整（若仍偶发）
LLM_MAX_TOKENS=2048      # 限制生成长度，压缩循环空间
LLM_TEMPERATURE=0.7      # 保持默认，勿调高

---

## 新增问题（第八轮补 - 生产环境修复 #9）— bug-102 根因纠正

> 触发场景：应用 bug-102 的 prompt 防重复指令并确认 `LLM_MAX_TOKENS=4096 / LLM_TEMPERATURE=0.7` 后，
> 重启仍复现：提问"有什么文物"推荐 **195 件文物且全部重复**。
> 195 件 = 39 轮完整重复，远超 max_tokens=4096 的单次生成能力 → **判定 bug-102 根因有误**。
> 深度排查（读 dashscope 1.25.1 源码）确认真正根因：
> **dashscope 流式默认"合并模式"（incremental_to_full）——每个 chunk 的 content 是到当前为止的累积全文，
> 而非增量 token。代码按增量追加（`full_answer += chunk`）导致内容翻倍膨胀重复。**
> 全量测试：`pytest tests/ -q` → **211 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-103 | dashscope 流式默认合并模式：未传 `incremental_output=True` 时，每个流式 chunk 的 content 为累积全文，`full_answer += chunk` 按增量追加 → 内容膨胀重复（实测 195 件文物循环） | `src/llm.py` | P0 | 已修复 |

## 问题详情

### [bug-103] dashscope 流式合并模式导致内容膨胀重复（P0，bug-102 真实根因）

- **根因分析**：读 dashscope 1.25.1 `Generation.call` 源码：
  ```python
  is_incremental_output = kwargs.get('incremental_output', None)          # 未传 → None
  if (ParamUtil.should_modify_incremental_output(model) and
          is_stream and is_incremental_output is False):
      to_merge_incremental_output = True                                  # 自动进入合并模式
      parameters['incremental_output'] = True
  ...
  if is_stream:
      if to_merge_incremental_output:
          return cls._merge_generation_response(response, n)              # ← 每个 chunk 为累积全文
      else:
          return (GenerationResponse.from_api_response(rsp) for rsp in response)  # ← 增量 token
  ```
  对 qwen 系列模型，只要 `stream=True` 且未显式传 `incremental_output`，SDK 自动进入**合并模式**：
  生成器每个元素是"到当前为止的完整文本"。`chat_stream` 中 `full_answer += content` 按增量追加，
  累积全文被反复拼接 → O(n²) 膨胀。本地复现：70 字回答在合并模式下膨胀为 160 字、"司母戊鼎"出现 3 次。
  生产实测 195 件重复（39 轮 × 5 件）。**本地测试全部 mock 流式，从未真实调用 dashscope 流式，故未暴露。**
- **影响范围**：所有 `chat_stream` 场景（Web UI 流式模式、SDK 流式调用）。回答内容膨胀重复，浪费 token 与显示。
- **修复方案**：`chat_stream` 的 `Generation.call(..., stream=True, incremental_output=True)`，
  显式要求增量输出（每个 chunk 的 content 为独立 token 增量）。传 `incremental_output=True` 后
  `to_merge_incremental_output=False`，SDK 返回增量生成器，`full_answer += token` 正确拼接。
- **风险分析**：低。`incremental_output` 为百炼 API 标准参数（服务端参数，SDK 透传），
  requirements 约束 `dashscope>=1.20.0` 均支持；非流式 `chat` 不受影响。
- **测试验证**：新增 `TestStreamingIncrementalOutput`（3 项）：源码断言 `incremental_output=True`、
  增量 token 拼接无重复且参数正确传给 SDK、防御性验证累积模式必然膨胀。全部通过；`pytest tests/ -q` → **211 passed**。

> **说明**：bug-102（prompt 防重复指令）判定为"LLM 生成循环"有误，真实根因为本 bug（dashscope 流式合并模式）。
> prompt 防重复指令仍保留（对模型生成有轻微正向约束，不冲突），但不再作为循环问题的修复依据。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-103 | 源码确认 dashscope 合并模式逻辑；本地复现累积膨胀；修复后增量 token 拼接无重复；`TestStreamingIncrementalOutput`（3 项）；211 passed | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **211 passed**（0 失败 0 错误）。

## 验证步骤（第八轮补）

### 本地复现与修复验证
1. `python -c` 模拟合并模式（累积 chunk）按增量追加 → 内容膨胀（复现 bug）
2. `python -c` mock 增量 chunk 流 + 断言 `incremental_output=True` 传入 → 拼接结果无重复
3. `pytest tests/test_review_findings.py::TestStreamingIncrementalOutput -v` → 3 passed

### 生产环境操作指引
1. 同步 `src/llm.py` 到服务器；
2. 重启 `python app.py --project museum --host 0.0.0.0 --port 7860`；
3. 页面提问"有什么文物"→ 应只返回 3~5 件推荐，无重复。

---

## 新增问题（第八轮补 - 生产环境修复 #10）

> 触发场景：Web UI 多轮对话（第二轮起）提问开放类问题，页面报错。后台：
> `File "app.py", line 167, in _convert_history → marker_idx = assistant_msg.find(marker)`
> `AttributeError: 'list' object has no attribute 'find'`
> 根因：**Gradio 6.0 的 Chatbot.preprocess 会把消息 content 从 str 转为 list[dict] 多模态格式**
> （`[{"type": "text", "text": "..."}]`）。多轮对话时 `_convert_history` 收到的 history 中
> 每条 content 均为 list，调用 `.find()` 崩溃。第一轮 history 为空故正常，第二轮起必现。
> 全量测试：`pytest tests/ -q` → **214 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-104 | Gradio 6 Chatbot.preprocess 将消息 content 转为 list[dict]，`_convert_history` 对 list 调用 `.find()` 崩溃（多轮对话第二轮起必现） | `app.py` | P0 | 已修复 |

## 问题详情

### [bug-104] Gradio 6 多模态 content 格式导致多轮对话崩溃（P0）

- **根因分析**：读 Gradio 6.22 `chatbot.py` 源码确认：
  ```python
  # gradio 6.x Chatbot.preprocess（前端数据 → 事件函数入参）
  message_dict["content"] = [
      self._preprocess_content(content) for content in message.content
  ]   # ← content 从 str 强制转为 list[NormalizedMessageDict]
  ```
  多轮对话时，事件函数收到的每条消息 content 均为 `[{"type": "text", "text": "..."}]` 形式。
  `_iter_history_pairs` 原样取出该 list，`_convert_history` 中 `assistant_msg.find(marker)`
  对 list 调用 `.find()` → `AttributeError`。第一轮 history 为空不触发；第二轮起必现
  （用户先问推荐问题成功，之后再问任何问题均报错）。
- **影响范围**：Gradio 6 环境下的**所有多轮对话**（第二轮起任何问题）。
- **修复方案**：新增 `_extract_text(content)` helper，兼容 str / list[dict]（多模态）/ 数字 / None，
  统一提取为文本；`_iter_history_pairs` 对 dict 与 tuple 两种格式的 content 均应用之。
- **风险分析**：低。仅增加 content 类型归一化；纯 str 路径行为不变。
- **测试验证**：新增 `TestGradio6ListContentHistory`（3 项）：list content 归一化、
  混合类型不崩溃（含 bug-028 空回复删除语义）、两轮对话完整流程不崩溃。全部通过；
  `pytest tests/ -q` → **214 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-104 | `TestGradio6ListContentHistory`（3 项）通过；两轮对话（Gradio 6 list content）完整流程无崩溃；214 passed | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **214 passed**（0 失败 0 错误）。

## 验证步骤（第八轮补）

### 本地自测
1. `python -c` 构造 Gradio 6 preprocess 格式 history（content 为 `[{"type":"text","text":...}]`），
   调 `_convert_history` → 正确提取文本、不崩溃
2. `python -c` mock pipeline 跑两轮对话（第二轮 history 为 list content）→ 无崩溃
3. `pytest tests/test_review_findings.py::TestGradio6ListContentHistory -v` → 3 passed

### 生产环境操作指引
1. 同步 `app.py` 到服务器；
2. 重启 `python app.py --project museum --host 0.0.0.0 --port 7860`；
3. 任意多轮对话（第二轮起）应正常回答。

---

## 新增问题（第八轮补 - 生产环境修复 #11）

> 触发场景：大量问题回答末尾声明"截止到 2024 年 7 月"，与用户当前时间（2026 年）不符，
> 用户要求不要出现该类表述。全仓 grep 无 "2024/截止" 字样 → 属 **LLM 训练数据知识截止日期的内生声明**，
> 非代码硬编码。修复：system prompt 统一注入**当前日期**（今天 2026年8月6日）并禁止截止类表述。
> 全量测试：`pytest tests/ -q` → **217 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-105 | 模型回答声明"截止到2024年7月"等训练数据知识截止日期，与当前时间不符 | `src/llm.py` | P1 | 已修复 |

## 问题详情

### [bug-105] 模型声明训练数据知识截止日期（P1）

- **根因分析**：qwen 系列模型回答时效性问题时，习惯基于训练数据知识截止日期（如 2024-07）
  声明"截止到XX年XX月"，且无当前时间概念。代码中无任何 "2024/截止" 硬编码（已 grep 确认）。
- **影响范围**：所有 LLM 回答（Web UI / SDK / 闲聊），时效性类问题尤其明显。
- **修复方案**：`BailianLLM._build_messages` 在 system prompt 统一追加当前日期说明：
  - 注入 `【当前日期】今天是{YYYY年M月D日}`（跨平台 `datetime.now()` 拼接，非 `strftime %-m`）；
  - 指令：以当前日期为准、不要声明"截止到XX年XX月 / 我的知识截止于XX / 截至XX年"等表述、
    时效无法确认时说明"建议以官方最新发布为准"。
  - 无 system_prompt 的纯消息调用不注入（不影响）。
- **风险分析**：低。仅追加 system prompt 文本；`_select_prompt` 层测试断言不受影响
  （它们断言 rag_pipeline 层 prompt 选择，不经过 `_build_messages`）。
- **测试验证**：新增 `TestCurrentDateInjection`（3 项）：注入当前日期与禁止指令、
  无 system_prompt 不注入、chat/chat_stream 均注入。全部通过；`pytest tests/ -q` → **217 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-105 | `TestCurrentDateInjection`（3 项）通过；当前日期（2026年8月6日）正确注入 system prompt；217 passed | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **217 passed**（0 失败 0 错误）。

## 验证步骤（第八轮补）

### 本地自测
1. `python -c` 调 `_build_current_date_note()` → 含"今天是2026年8月6日"与禁止截止指令
2. `python -c` 调 `_build_messages(system_prompt=...)` → system content 尾部含日期说明，user 消息不受影响
3. `pytest tests/test_review_findings.py::TestCurrentDateInjection -v` → 3 passed

### 生产环境操作指引
1. 同步 `src/llm.py` 到服务器；
2. 重启 `python app.py --project museum --host 0.0.0.0 --port 7860`；
3. 提问时效性问题 → 不应再出现"截止到2024年7月"类表述；时效无法确认时提示以官方最新发布为准。

---

## 新增问题（第八轮补 - 生产环境修复 #12）

> 触发场景：用户提出开放类/时效性问题（"最近情况""现在怎么样"），模型只能基于训练数据
> （截止 2024-07）回答，无法获取 2026 年新信息。确认百炼 API 支持 `enable_search=True`
> （dashscope 1.25.1 官方注释：Whether to enable web search (quark)）后，按方案 B 实施
> **按需自动联网搜索**。全量测试：`pytest tests/ -q` → **220 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-106 | 模型无法获取当前（2026）真实新数据，开放类/时效性问题只能基于训练数据回答 | `src/config.py`, `src/llm.py`, `src/rag_pipeline.py`, `.env.example` | P1 | 已修复 |

## 问题详情

### [bug-106] 按需自动联网搜索（方案 B）（P1）

- **根因分析**：模型训练数据截止 2024-07，时效性问题（展览/门票/最新动态/2026 年情况）
  无法回答最新信息。百炼 API 支持 `enable_search=True`（内置夸克联网搜索），但需按次计费，
  全量开启成本高、响应慢。
- **修复方案**（方案 B：按需自动联网）：
  1. `src/config.py` 新增 `llm_enable_search: bool = False`（总开关，默认关避免误扣费）；
  2. `src/rag_pipeline.py` 新增 `TEMPORAL_KEYWORDS`（最新/最近/今年/展览/门票/2026 等 30+ 词）
     与 `_should_enable_search(query_type, question)` 判断规则：
     - `OPEN_ENDED`（开放讨论）→ 联网；
     - `UNKNOWN`（未分类/非知识库）→ 除纯问候语（你好/谢谢/再见…）外联网；
     - `FACTUAL/RECOMMENDATION/COMPARISON/CHITCHAT` → 命中时效关键词才联网；
  3. `query/query_stream` 各 LLM 调用点计算 `enable_search = settings.llm_enable_search and 按需判断`，
     传入 `llm.chat/chat_stream`；meta/返回结果带 `search_enabled` 字段便于前端提示；
  4. `src/llm.py` 的 `chat/chat_stream` 新增 `enable_search` 参数透传 `Generation.call`；
     `chat` 将其并入 `call_kwargs` 参与缓存 key（搜索/不搜索回答不混用缓存）；
     启用时 system prompt 追加 `【联网搜索】` 引导：联网结果仅补充时效信息，
     文物知识以 RAG 参考信息为准，避免与网上信息冲突。
- **风险分析**：中。① 费用：按次计费，总开关默认关闭，开启后按需触发；② 时效词可能偶发误伤
  知识库问题（如"司母戊鼎现在在哪里"）→ 多搜一次但 RAG 上下文+搜索引导保证知识仍以参考信息为准；
  ③ 需百炼账号开通联网搜索能力（该账号重排模型曾受限，需控制台确认）。
- **测试验证**：新增 `TestOnDemandWebSearch`（3 项）：按需判断规则、chat_stream 透传+引导注入、
  总开关开/关下 meta 的 search_enabled 与透传。全部通过；`pytest tests/ -q` → **220 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-106 | `TestOnDemandWebSearch`（3 项）通过；开放/时效问题联网、问候语与纯事实问题不联网；总开关生效；220 passed | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **220 passed**（0 失败 0 错误）。

## 验证步骤（第八轮补）

### 本地自测
1. `python -c` 调 `_should_enable_search`：开放类/时效词→True，问候语/纯事实→False
2. `python -c` mock 流式：`enable_search=True` 透传 SDK 且 system prompt 含【联网搜索】引导
3. `pytest tests/test_review_findings.py::TestOnDemandWebSearch -v` → 3 passed

### 生产环境操作指引
1. 百炼控制台确认 qwen-plus 已开通"联网搜索"能力；
2. 同步 `src/config.py`、`src/llm.py`、`src/rag_pipeline.py` 到服务器；
3. 服务器 `.env` 设 `LLM_ENABLE_SEARCH=true`（按次计费，确认费用预期）；
4. 重启 `python app.py --project museum --host 0.0.0.0 --port 7860`；
5. 提问"最近有什么特展/门票多少钱/2026年有什么新动态"→ 自动联网获取最新信息；
   提问"司母戊鼎是什么时期的"→ 不联网，走知识库。

---

## 新增问题（第八轮补 - 生产环境修复 #13）

> 触发场景：Web UI 点击"刷新状态"按钮，报错"知识库状态: 'CollectionParams' object has no attribute 'distance'"。
> 根因：qdrant-client 1.10+（本项目 1.19.0）将 distance 从 CollectionParams 顶层移入
> params.vectors（单向量为 VectorParams / 命名向量为 VectorParamsMap），
> `get_stats` 仍读 `config.params.distance` 导致 AttributeError。
> 全量测试：`pytest tests/ -q` → **223 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-107 | qdrant-client 1.10+ CollectionParams 无顶层 distance，get_stats 崩溃（页面刷新状态报错） | `src/rag_pipeline.py` | P1 | 已修复 |

## 问题详情

### [bug-107] qdrant-client 1.10+ CollectionParams 结构变更（P1）

- **根因分析**：本地实测 qdrant-client 1.19.0：`CollectionParams` 无 `distance` 属性
  （`hasattr(p, 'distance') → False`），距离度量在 `params.vectors.distance`
  （单向量为 `VectorParams`，命名向量为 `VectorParamsMap`）。`get_stats` 读
  `collection_info.config.params.distance` → AttributeError，页面状态栏报错。
- **影响范围**：Web UI"刷新状态"、`pipeline.get_stats()`（run_qa `/stats` 命令）。
- **修复方案**：`get_stats` 防御性读取：
  - `distance`：优先顶层（旧版），否则单向量 `vectors.distance`，命名向量取第一个配置；
  - `vector_size`：同理兼容（命名向量时 `vectors` 为 dict 无 `size`）。
- **风险分析**：低。仅读取层兼容，不改变写入/检索逻辑；旧版与新版结构均正常。
- **测试验证**：新增 `TestCollectionStatsCompat`（3 项）：新结构单向量、旧结构顶层 distance、
  命名向量 VectorParamsMap。全部通过；`pytest tests/ -q` → **223 passed**。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-107 | `TestCollectionStatsCompat`（3 项）通过；本地实测新旧/命名向量三种结构均正确；223 passed | ✅ 已修复 |

**全量测试**：`pytest tests/ -q` → **223 passed**（0 失败 0 错误）。

## 验证步骤（第八轮补）

### 本地自测
1. `python -c` 构造 `CollectionParams(vectors=VectorParams(...))`（1.19.0 结构）→ get_stats 正常
2. `python -c` 构造旧结构（顶层 distance）与命名向量 → 均正常
3. `pytest tests/test_review_findings.py::TestCollectionStatsCompat -v` → 3 passed

### 生产环境操作指引
1. 同步 `src/rag_pipeline.py` 到服务器；
2. 重启 `python app.py --project museum --host 0.0.0.0 --port 7860`；
3. 页面点击"刷新状态"→ 正常显示向量数/维度/距离度量。

---

## 新增问题（第八轮补 - 生产环境修复 #14）

> 触发场景：Web UI 对话窗口与检索区域"一闪"后消失变空白；浏览器 F12 仅见
> `<label for=FORM_ELEMENT>` 警告（旧 gradio 前端固有缺陷，非根因）。
> 排查过程：① 服务器 gradio 前端资源为旧版本残留（index-BZvZc4Wo.js ≠ 本地 index-BgYNBSAi.js），
> 彻底重装 gradio 6.22.0 后资源对齐（index-BgYNBSAi.js）仍崩；
> ② dump config 发现 **Gradio 6 将 avatar_images 的 emoji 字符串 "🏛️" 当作文件路径解析
> 为 FileData**（path 形如 `.../🏛️`，不存在）→ 前端渲染 Chatbot 时请求无效文件 → 组件区域崩溃。
> 修复：移除 emoji 头像（avatar_images=None）。
> 全量测试：`pytest tests/ -q` → **223 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-108 | Gradio 6 将 avatar_images 的 emoji 字符串解析为无效文件路径 FileData，前端渲染 Chatbot 崩溃（对话+检索区域消失） | `app.py` | P0 | 已修复 |

## 问题详情

### [bug-108] Gradio 6 emoji 头像被解析为无效文件路径（P0）

- **根因分析**：`gr.Chatbot(avatar_images=(None, "🏛️"))` 在 Gradio 6 中，字符串参数被当作
  文件路径处理：config 生成 `FileData(path=".../🏛️")`（路径不存在）。前端渲染 Chatbot 时
  请求该无效文件导致组件渲染崩溃，对话窗口与检索区域（同一 Row）整体消失。
  本地同代码 curl 验证仅看 HTML 结构（不执行 JS），未暴露；服务器浏览器实测复现。
  附带发现：服务器 gradio 前端资源曾为旧版本残留（index-BZvZc4Wo.js），伴随旧版
  `<label for=FORM_ELEMENT>` 警告，重装 gradio 6.22.0 后资源对齐（index-BgYNBSAi.js），
  但 avatar 问题仍存在——两者独立。
- **影响范围**：Gradio 6 环境下 Web UI 对话区域整体不可用。
- **修复方案**：移除 emoji 头像，`avatar_images=None`（gradio 归一化为 [None, None]，无 FileData）。
- **风险分析**：低。仅移除装饰性头像，对话功能不受影响。
- **测试验证**：APP 构建后 dump config：Chatbot avatar 为 [None, None] 且无无效路径；
  全量测试 223 passed。

## 验证结果（第八轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-108 | config dump 确认 avatar 无 FileData；223 passed；待服务器浏览器实测确认 | ✅ 已修复（待服务器确认） |

**全量测试**：`pytest tests/ -q` → **223 passed**（0 失败 0 错误）。

## 验证步骤（第八轮补）

### 本地自测
1. `python -c` 构建 create_ui → dump config → Chatbot avatar 为 [None, None]（无无效路径）
2. `pytest tests/ -q` → 223 passed

### 生产环境操作指引
1. 同步 `app.py` 到服务器；
2. 重启 `python app.py --project museum --host 0.0.0.0 --port 7860`；
3. 本机【无痕窗口】访问 http://10.0.2.200:7860/ → 对话窗口与检索区域应正常显示；
4. 若仍异常，F12 Console 查看新错误并反馈。

---

## 验证补充（bug-107 / bug-108 服务器确认）

> 服务器实测结论：页面恢复正常。此前"对话窗口与检索区域消失"经排查为**用户误读**
> （对话窗口位于页面下方滚动区，未滚动到导致误以为消失），非代码缺陷。
> 但排查过程中确认并修复的以下问题均为**真实存在且有价值**，予以保留：
> 1. bug-107：get_stats 的 qdrant-client 1.10+ 结构兼容（刷新状态报错真实存在，已修复）
> 2. bug-108：avatar emoji 被 Gradio 6 解析为无效 FileData 路径（config 实测存在，
>    已移除，避免前端渲染隐患）
> 3. 服务器 gradio 前端资源旧版本残留（index-BZvZc4Wo.js ≠ 本地 index-BgYNBSAi.js，
>    FORM_ELEMENT 警告来源）已通过重装 gradio 6.22.0 对齐
> 最终状态：服务器 Web UI 正常，对话/检索区域可正常使用；223 passed。

---

## 新增功能（第九轮 - Excel 数据源支持）

> 需求来源：多个项目（家博会等）拥有大量 Excel 表格数据（参展商名单、展位信息等），
> 原系统仅支持 JSON/CSV 结构化数据与多格式文档，无法直接使用 `.xlsx`。
> 设计文档：`docs/superpowers/specs/2026-08-06-excel-support-design.md`（已批准方案 A）
> 全量测试：`pytest tests/ -q` → **232 passed**（原 223 + 新增 9，0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-109 | 新增功能：Excel (.xlsx) 表格数据作为知识库数据源（表格型，每行一条记录；多 sheet 支持；任意列可检索） | `src/data_loader.py`、`src/document_loader.py`、`requirements.txt` | 功能增强 | 已实现 |

## 问题详情

### [bug-109] Excel (.xlsx) 数据源支持（功能增强）

- **需求确认**：
  1. 形态为**表格型**（每行一条记录，列 = 字段）；
  2. 列名**不可穷举**（不同项目字段各异），需通用策略——未识别列全部入库可检索；
  3. 接入方式：**docs 模式**（目录自动识别）+ **json 模式**（`--json-path xxx.xlsx`）双入口；
  4. **多 sheet** 支持，每个 sheet 独立成数据集；旧版 `.xls` 不支持（仅 openpyxl 支持的 .xlsx）。
- **实现方案**（方案 A：共享核心 + 双入口委托）：
  1. `src/data_loader.py`：
     - `SUPPORTED_FORMATS` 增加 `"xlsx"`，`loader_map` 增加 `_load_xlsx`；
     - `_load_xlsx`（openpyxl `read_only=True, data_only=True`）：遍历所有 sheet，第一行=表头，
       后续行每行一条记录；单元格值统一转字符串（数字/布尔→str、日期→`YYYY-MM-DD`）；空行/空列跳过；
     - **名称列三级识别**：① 列名命中候选集（名称/name/标题/展商名称/企业名称/公司名称/项目名称…）
       → ② 否则取第一个非空列 → ③ 兜底 `"{sheet名}第N行"`；
     - **任意列可检索**：所有非 name 列以 `"列名：值"` 拼入 description → 任何列内容均可被全文检索命中；
     - `extra` 保留原始列数据 + `sheet` 名；
  2. `src/document_loader.py`：`SUPPORTED_EXTENSIONS` 增加 `.xlsx`；`load_all_as_artifacts`
     对 `.xlsx` 文件（单文件与目录两种路径）委托 `DataLoader.load`（Excel 为多记录文件，不走单文档模型）；
     load_directory 的 extension_map 不含 .xlsx，目录模式下由 `load_all_as_artifacts` 单独 glob 收集；
  3. `requirements.txt` 增加 `openpyxl>=3.1.0`（可选依赖，缺失时仅 Excel 功能不可用并给出友好 ImportError）。
- **风险分析**：低-中。新增独立解析路径，未改动现有 JSON/CSV/文档解析逻辑（`_normalize` 保持不变，
  通用列策略集中在 `_load_xlsx`，仅影响 Excel 路径）；openpyxl 缺失时友好报错不影响其他功能。
- **测试验证**：新增 `tests/test_edge_cases.py::TestExcelSupport`（9 项）：
  1. `DataLoader.load` 解析 .xlsx 返回正确条数与字段（标准列名）；
  2. 名称列识别：自定义列名（展商名称）命中候选集；
  3. 名称列兜底：无候选列时取第一个非空列；
  4. 任意未识别列（展位号/联系人电话）拼入 description 可检索命中；
  5. 多 sheet 各自独立处理，sheet 名进入 extra；
  6. 空行跳过；
  7. openpyxl 缺失时友好 ImportError（mock sys.modules）；
  8. docs 模式 `load_all_as_artifacts` 识别单个 .xlsx 文件；
  9. docs 模式目录中 .xlsx 与普通文档混合加载。
  全部通过；全量 `pytest tests/ -q` → **232 passed**（0 失败 0 错误）。

## 使用方式

```bash
# 方式一：docs 模式（.xlsx 直接放进文档目录，与 PDF/Word 混放）
python scripts/build_knowledge_base.py --project jiabohui --source docs --doc-path ./data/raw/jiabohui

# 方式二：json 模式（显式指定 Excel 文件，注意脚本默认只找 data.json/artifacts.json，必须 --json-path）
python scripts/build_knowledge_base.py --project jiabohui --source json --json-path ./data/raw/jiabohui/参展商名单.xlsx

# 方式三：mixed 模式（Excel 结构化数据 + 文档合并）
python scripts/build_knowledge_base.py --project jiabohui --source mixed \
  --json-path ./data/raw/jiabohui/参展商名单.xlsx \
  --doc-path ./data/raw/jiabohui
```

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-109 | `TestExcelSupport`（9 项）通过；综合实测（多 sheet/数字/日期/布尔/空行/自定义列名）通过；全量 232 passed | ✅ 已实现 |

---

## 新增变更（第九轮补 - Embedding 模型升级 text-embedding-v3 → text-embedding-v4）

> 需求来源：用户要求将项目中使用的 text-embedding-v3 全部升级为 text-embedding-v4，API Key 不变，确保项目正常运行。
> 全量测试：`pytest tests/ -q` → **236 passed**（原 232 + 新增 4，0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-110 | 变更：Embedding 模型从 text-embedding-v3 升级为 text-embedding-v4（API Key 不变） | `src/config.py`、`src/embeddings.py`、`.env.example`、README/project-context/DEPLOY_GUIDE | 模型升级 | 已完成 |

## 问题详情

### [bug-110] Embedding 模型升级 text-embedding-v3 → text-embedding-v4（模型升级）

- **变更范围**：
  1. `src/config.py`：`embedding_model_name` 默认值 `text-embedding-v3` → `text-embedding-v4`；
  2. `src/embeddings.py`：`BailianEmbedding.__init__` 默认 `model` 参数 `text-embedding-v3` → `text-embedding-v4`；
     注释同步更新（MAX_BATCH_SIZE=10 的 API 限制说明覆盖 v3/v4）；
  3. `.env.example`：`EMBEDDING_MOD_NAME=text-embedding-v3` → `EMBEDDING_MODEL_NAME=text-embedding-v4`
     （**顺带修正键名拼写**：原 `EMBEDDING_MOD_NAME` 缺 `EL`，与代码字段 `embedding_model_name`
     不对应，按模板配置的用户该配置实际不生效）；
  4. 文档：README.md（技术栈表/数据流/FAQ）、project-context.md（技术栈/模型选择/环境变量）、
     DEPLOY_GUIDE.md（API 连通性测试命令）同步更新；历史更新日志（v1.3.x 中 v3 相关描述）保留原样。
- **兼容性说明**：text-embedding-v4 与 v3 调用契约一致（`TextEmbedding.call(model, input, dimension, api_key)`），
  单请求上限同为 10 条（MAX_BATCH_SIZE 钳制逻辑继续生效），维度支持同为 1024/768/512/256/128/64（默认 1024 不变）。
- **风险分析**：低。仅模型名变化，API 调用方式/参数/批大小/维度均不变；`.env` 存量配置若显式写了
  `EMBEDDING_MODEL_NAME=text-embedding-v3` 不会被覆盖（环境变量优先于默认值），需用户手动更新或删除该行。
- **测试验证**：新增 `tests/test_edge_cases.py::TestEmbeddingModelUpgrade`（4 项）：
  1. settings 默认模型为 v4；2. BailianEmbedding 默认 model 为 v4（与 settings 一致）；
  3. RAGPipeline 构造使用 settings 模型名（升级后自动生效）；4. 批大小仍 ≤ API 上限 10。
  全部通过；全量 `pytest tests/ -q` → **236 passed**（0 失败 0 错误）。

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-110 | `TestEmbeddingModelUpgrade`（4 项）通过；全量 236 passed；语法检查通过 | ✅ 已完成 |

## 服务器操作指引

1. 同步 `src/config.py`、`src/embeddings.py`、`.env.example` 到服务器；
2. 检查服务器 `.env`：若显式配置了 `EMBEDDING_MODEL_NAME=text-embedding-v3`，改为 `text-embedding-v4`
   （或删除该行使用默认值）；若配置的是旧拼写 `EMBEDDING_MOD_NAME`（不生效），改为 `EMBEDDING_MODEL_NAME`；
3. 重新构建或查询即使用 text-embedding-v4（API Key 不变）；
   ⚠️ 注意：v3 与 v4 向量维度同为 1024 但**向量空间不同**，已用 v3 构建的 Qdrant 数据需**重新构建知识库**，
   否则新旧向量混用会导致检索质量下降。

---

## 新增问题（第九轮补 - Web UI 项目下拉框误切换）

> 触发场景：服务器 `python app.py --project jiabohui --port 7860` 启动后提问"你是谁"，
> 返回的是博物馆回答。日志显示启动后 9 秒出现 `初始化 RAG 流水线 - 项目: museum`。
> 全量测试：`pytest tests/ -q` → **239 passed**（原 236 + 新增 3，0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-111 | Web UI 项目下拉框 choices/value 硬编码（museum/enterprise），`--project jiabohui` 启动后页面加载（demo.load → get_system_status）把全局 pipeline 误切换成 museum | `app.py` | P0 | 已修复 |

## 问题详情

### [bug-111] Web UI 项目下拉框硬编码导致启动项目被误切换（P0）

- **根因分析**：`app.py` 的 `create_ui()` 中项目下拉框：
  ```python
  project_dropdown = gr.Dropdown(
      choices=[("博物馆知识库", "museum"), ("企业知识库", "enterprise")],  # 硬编码，不含动态项目
      value="museum",   # 硬编码默认值，与 --project 启动参数无关
  )
  ```
  服务启动流程：`main()` 先 `init_pipeline(args.project)`（jiabohui ✅）→ `create_ui()` → 页面加载时
  `demo.load(get_system_status, [project_dropdown], ...)` 触发 `get_system_status(dropdown.value)`，
  dropdown 默认值为硬编码的 `"museum"` → `init_pipeline("museum")` **把全局 pipeline 切换成 museum**。
  之后所有提问（含闲聊"你是谁"）都走 museum pipeline，返回博物馆人设回答。
  服务器日志佐证：`03:57:19` 启动 jiabohui（171 切片）→ `03:57:28` 页面加载切换 museum（38 切片）→ `03:57:41` 闲聊返回博物馆回答。
  自定义/外部项目（jiabohui）也不会出现在下拉框（choices 硬编码）。
- **影响范围**：所有 `--project <自定义项目>` 启动的 Web UI；页面加载后全局 pipeline 被切换，
  回答全部变成默认项目（museum）的内容与人设。
- **修复方案**：
  1. `create_ui(default_stream=True, default_project="")` 新增 `default_project` 参数（= `--project` 参数值）；
  2. 下拉框 `choices` 改为动态来自 `project_manager.list_projects()`（含自定义/外部项目）；
  3. 下拉框 `value` = `default_project`（若在项目列表中）否则第一个项目（保持向后兼容，默认 museum）；
  4. `main()` 传 `create_ui(default_stream=..., default_project=args.project)`。
  修复后：`--project jiabohui` 启动 → 下拉框默认选中 jiabohui → 页面加载状态检查仍用 jiabohui → 不误切换。
- **风险分析**：低。默认不传 `--project` 时 value 仍为 museum（行为不变）；下拉框现在显示全部项目（正确行为）。
- **测试验证**：新增 `tests/test_edge_cases.py::TestProjectDropdownUI`（3 项）：
  1. choices 来自 `project_manager.list_projects()`（含动态项目）；
  2. 不传 default_project 时默认值仍为 museum（向后兼容）；
  3. 注入 jiabohui 项目后 `create_ui(default_project="jiabohui")` → value 为 jiabohui 且出现在 choices。
  场景验证：mock 注入 jiabohui → `init_pipeline("jiabohui")` → `create_ui(default_project="jiabohui")` →
  页面加载 `get_system_status(dropdown.value)` 后全局 pipeline 仍为 jiabohui（未被切换）。
  全部通过；全量 `pytest tests/ -q` → **239 passed**（0 失败 0 错误）。

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-111 | `TestProjectDropdownUI`（3 项）通过；场景验证（启动 jiabohui + 页面加载不误切换）通过；全量 239 passed | ✅ 已修复 |

## 服务器操作指引

1. 同步 `app.py` 到服务器；
2. 重启 `python app.py --project jiabohui --host 0.0.0.0 --port 7860`；
3. 页面加载后提问"你是谁"应返回"我是小虎"（家博会人设），不再返回博物馆回答；
4. 下拉框应显示全部项目（博物馆知识库/企业知识库/家博会数字人小虎），默认选中家博会数字人小虎。

---

## 新增问题（第九轮补 - 推荐类回答混入不相关结果）

> 触发场景：jiabohui 项目提问"我要买沙发，推荐几个展位给我"，回答推荐了 5 个展位，
> 前 3 个为沙发展位（正常），后 2 个为设计品牌展位（前进觅美/巴博罗，与买沙发需求不直接相关）。
> 全量测试：`pytest tests/ -q` → **242 passed**（原 239 + 新增 3，0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-112 | 推荐类回答混入与用户需求不相关的展位：服务器 qwen3-reranker-4b 未开通（重排降级本地 TF-IDF，字符级无法语义区分"沙发品牌"与"设计品牌"）+ recommend prompt 无相关性过滤约束（LLM 硬凑推荐数） | `src/rag_pipeline.py`、`src/project.py` | P1 | 已修复（prompt 层） |

## 问题详情

### [bug-112] 推荐类回答混入不相关结果（P1）

- **根因分析**（两个因素叠加）：
  1. **主因（环境）**：服务器账号未开通 `qwen3-reranker-4b`（此前 bug-097 日志实证 `400 - Model not exist`），
     重排序一直降级到本地 TF-IDF（字符级 n-gram 1-3）。本地实验复现："我要买沙发，推荐几个展位给我"
     的 TF-IDF 重排结果中，"前进觅美"（文本含"模块化组合沙发"）被排第 2、"巴博罗"（无沙发字样）排第 5，
     与用户实测排序一致——字符级重排无法语义区分"沙发品牌"与"设计品牌"。
  2. **次因（代码）**：recommend prompt（默认 `SYSTEM_PROMPT_RECOMMEND` 与项目模板）只有
     "从参考信息中挑选 3~5 个最具代表性的结果"，**无相关性过滤约束**——LLM 拿到 5 个候选
     （含不相关项）时照单全收凑满推荐数。
- **影响范围**：所有推荐类问题（"推荐/有哪些/买什么"）在重排降级时可能混入不相关项；
  服务器当前重排恒为 TF-IDF 降级，影响实况存在。
- **修复方案**：
  1. **代码（prompt 相关性过滤）**：默认 `SYSTEM_PROMPT_RECOMMEND` 与内置 museum/enterprise
     recommend 模板增加第 3 条："**相关性优先**：只推荐与用户问题**直接相关**的项；参考信息中与
     用户需求不相关的项**不要推荐**（宁缺毋滥，不要为凑满数量硬推）"；
  2. **环境（根治重排质量）**：开通 `qwen3-reranker-4b`，或 `.env` 改 `RERANKER_MODEL` 为账号已开通的重排模型。
- **风险分析**：低。仅添加 prompt 指令文本，`{context}` 占位符保留；不影响检索/入库逻辑。
- **测试验证**：新增 `tests/test_edge_cases.py::TestRecommendPromptRelevance`（3 项）：
  1. 默认 `SYSTEM_PROMPT_RECOMMEND` 含相关性过滤指令；
  2. `_select_prompt(RECOMMENDATION)` 返回的 prompt 含该指令；
  3. 内置 museum/enterprise recommend 模板含该指令且保留 `{context}`。
  全部通过；全量 `pytest tests/ -q` → **242 passed**（0 失败 0 错误）。

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-112 | `TestRecommendPromptRelevance`（3 项）通过；TF-IDF 降级实验复现排序问题（根因确认）；全量 242 passed | ✅ 已修复（prompt 层） |

## 服务器操作指引

1. 同步 `src/rag_pipeline.py`、`src/project.py` 到服务器；
2. **jiabohui 项目使用自定义 prompts**（服务器 `data/projects/jiabohui.json`），需手动给 recommend 模板
   增加同款相关性过滤指令（编号顺延）：
   ```json
   "recommend": "…\n## 推荐原则\n1. …\n2. …\n3. **相关性优先**：只推荐与用户问题**直接相关**的项；参考信息中与用户需求不相关的项**不要推荐**（宁缺毋滥，不要为凑满数量硬推）\n4. …（原编号顺延）"
   ```
3. **根治重排质量（强烈建议）**：百炼控制台开通 `qwen3-reranker-4b`，或 `.env` 设
   `RERANKER_MODEL` 为已开通模型（如 qwen3-reranker-8b / 其他可用 rerank 模型），
   并确认日志不再出现 `Qwen3-Reranker API 异常: 400`（此后重排由语义模型精排，相关问题不再混入）。

---

## 新增问题（第九轮补 - bug-112 根因更正 + prompt 品类匹配增强）

> 背景：用户确认服务器 `.env` 配置为 `RERANKER_MODEL=qwen3-rerank`（非 qwen3-reranker-4b），
> 日志实证 `Qwen3-Reranker 重排序完成` 达 17+ 次（与百炼控制台 17 次成功调用一一对应）——
> **重排 API 实际生效，未降级 TF-IDF**。此前 bug-112 将主因定为"qwen3-reranker-4b 未开通→降级 TF-IDF"
> 的判断不成立，特此更正。
> 全量测试：`pytest tests/ -q` → **243 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-112 | **根因更正**：重排 API（qwen3-rerank）已生效；"前进觅美/巴博罗"排进前 5 的真实原因为①数据层面两品牌确有沙发相关产品（语义重排判定相关）②recommend prompt 无品类匹配约束。**prompt 增强**：相关性指令升级为按用户需求"品类匹配"筛选 | `src/rag_pipeline.py`、`src/project.py` | P1 | 已修复（prompt 层） |

## 问题详情

### [bug-112 更正 + 增强] 推荐混入不相关项的根因修正与品类匹配指令（P1）

- **根因更正**：服务器重排实际使用 `qwen3-rerank`（API 生效，日志 17+ 次"重排序完成"），
  非此前判断的"未开通→TF-IDF 降级"。真实原因：
  1. **数据层**：前进觅美（模块化组合沙发）/巴博罗（软体家具线）在知识库文本中确有沙发相关内容，
     语义重排判定与"买沙发"相关度足够，排进前 5 是模型判断结果；
  2. **prompt 层**：recommend prompt 无品类匹配约束，LLM 拿到候选后硬凑推荐数。
- **修复方案（仅 prompt 增强，其他层暂不操作）**：默认 `SYSTEM_PROMPT_RECOMMEND` 与内置
  museum/enterprise recommend 模板第 3 条升级为：
  > "**相关性优先**：只推荐与用户问题**直接相关**的项；若参考信息中标明了**品类/类型/类别**，
  > 优先推荐与用户需求品类匹配的项，品类明显不匹配的**不要推荐**（宁缺毋滥，不要为凑满数量硬推）"
  前提：知识库 chunk 文本含品类信息（如"主营品类/类别"），LLM 才能据此判断——jiabohui 展位数据
  若缺品类字样，需在后续数据层补充（本次不操作）。
- **风险分析**：低。仅 prompt 指令文本变化，`{context}` 占位符保留。
- **测试验证**：`TestRecommendPromptRelevance` 新增 `test_prompts_include_category_matching`
  （断言默认 + 内置模板含"品类"指令）；全量 `pytest tests/ -q` → **243 passed**。

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-112 | `TestRecommendPromptRelevance`（4 项）通过；全量 243 passed；服务器日志确认重排 API 生效（非 TF-IDF） | ✅ 已修复（prompt 层） |

## 服务器操作指引

1. 同步 `src/rag_pipeline.py`、`src/project.py` 到服务器；
2. jiabohui 项目使用自定义 prompts（服务器 `data/projects/jiabohui.json`），recommend 模板
   需手动把第 3 条改为同款"品类匹配"指令（编号顺延）；
3. 重启服务验证：`python app.py --project jiabohui --host 0.0.0.0 --port 7860`，
   再问"我要买沙发，推荐几个展位给我"——LLM 应依据品类匹配过滤设计品牌展位。

---

## 新增功能（第十轮 - 意图理解分层分类 L0 规则 + L1 语义 + L2 LLM 兜底）

> 需求：用户意图理解从"纯规则评分"升级为工业界主流的**分层级联**——
> 规则层挡高频闲聊（零成本）、语义分类层处理多数情形（语义泛化）、LLM 兜底模糊场景（高精度）。
> 设计确认：L0 规则保留（is_kb_related）+ L1 向量语义分类 + L2 LLM 兜底（低置信时）。
> 全量测试：`pytest tests/ -q` → **282 passed**（原 243 + 新增 39，0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-113 | 意图理解升级：新增 L1 向量语义分类（对 5 类意图做原型相似度分类）+ L2 LLM 兜底（低置信度时），L0 规则层保留；`classify_query` 评分制降级为末级兜底 | `src/intent_classifier.py`（新增）、`src/rag_pipeline.py`、`src/config.py` | 功能增强 | 已实现 |

## 设计说明

### 分层架构（cost-aware cascade）

```
用户问题
  │
  ▼
L0 规则 is_kb_related（保留原实现，零成本）
  ├─ False → 闲聊分支（直接 LLM，不检索）
  └─ True  → L1 语义分类（SemanticIntentClassifier）
              ├─ 置信度 ≥ 阈值(0.50，实测校准) → 采用（method=semantic）
              └─ 置信度 < 阈值 → L2 LLM 兜底（classify_with_llm）
                                  ├─ 成功 → 采用（method=llm）
                                  └─ 失败/无 Key/无法解析 → 规则评分 classify_query（method=rules）
  L1/L2 返回 chitchat → 转闲聊分支（可捕获规则层漏掉的闲聊）
```

### 各层实现要点

1. **L0（保留）**：`is_kb_related` 规则层不动；顺带补充"好的"关键词（"好的吧/嗯嗯好的" 等
   口头应答词此前漏判走 RAG，实测验证"好的文物有哪些"等含实质内容查询不受影响）。
2. **L1（新增 `src/intent_classifier.py::SemanticIntentClassifier`）**：
   - 5 类意图各 7~8 条**领域无关**原型问题（recommendation/factual/comparison/open_ended/chitchat，
     符合"代码泛化"约定）；
   - 原型向量懒加载（线程安全，首次 classify 时计算）+ 复用全局 EmbeddingCache 持久化
     （首次计算后重启进程命中磁盘缓存，查询零额外 API 成本）；
   - `classify(question)` → (intent, confidence)，余弦相似度取最高分；
   - embedding 失败/空问题 → (None, 0.0) 走下游兜底。
3. **L2（`classify_with_llm`）**：LLM 意图分类提示词（只输出一个英文类型词），
   无 API Key / 调用失败 / 输出无法解析 → None；子串匹配容忍 LLM 输出噪声。
4. **QueryType 新增 CHITCHAT** 枚举成员；`_classify_intent()` 返回 (query_type, method)，
   method ∈ {semantic, llm, rules} 供日志观察；query/query_stream 中 L1/L2 识别出
   chitchat 时转闲聊分支（与规则层闲聊行为一致）。
5. **配置（`src/config.py`）**：`INTENT_SEMANTIC_ENABLED`（默认 true）、
   `INTENT_SEMANTIC_THRESHOLD`（默认 **0.50**，真实 API 实测校准）、
   `INTENT_LLM_FALLBACK_ENABLED`（默认 true）。
6. **构建预计算**：`build_knowledge_base` 在 `precompute_patterns` 后同步预计算意图原型向量
   （try/except 包裹，失败不影响构建）。

## 风险分析

- **低**。L1/L2 均有完整兜底链（embedding 失败 → L2 → 规则），任何一层不可用自动降级，
  最坏情况回到原 `classify_query` 行为（与修复前一致）；
- L2 按次计费：仅 L1 低置信度查询触发（实测约 25% 的模糊问题），总开关可关；
- 阈值 0.50 为真实 API 实测校准（同意图相似度区间 0.47~1.0，误分类置信度 0.28~0.55）；
- 已知边界："好的吧" 类口头应答由 L0"好的"关键词覆盖；极短退化输入（如"好的"单独出现）
  若 L0 未覆盖仍可能走一次空检索后 LLM 兜底，无崩溃风险。

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-113 | 新增 `tests/test_intent_classifier.py`（39 项）全部通过；真实 API 冒烟测试（L1 语义分类 + L2 LLM 兜底 + 完整级联端到端）全部通过；全量 282 passed | ✅ 已实现 |

### Mock 测试（39 项，`tests/test_intent_classifier.py`）
- **L1 单元**（13 项）：cosine_similarity 边界（相同/正交/相反/维度不一致/零向量/空）、
  classify 高置信/各类别逐个/低置信/空问题/embedding 失败/返回空、原型向量缓存、
  warmup 预计算、部分原型失败跳过；
- **L2 单元**（8 项）：成功/带噪声输出（"comparison。"）/前缀（"Intent: factual"）/
  无法解析/空输出/无 API Key 跳过/LLM 失败/prompt 含问题；
- **级联 `_classify_intent`**（9 项）：L1 高置信→semantic 且规则不调用、低置信→LLM、
  embedding 失败→LLM、LLM 失败→rules、无 Key→rules、semantic 关闭→rules、
  LLM 兜底关闭→rules、chitchat 映射、L1 关闭时规则调用；
- **query/query_stream 路由**（8 项）：知识库问题用语义结果、L1/L2 识别闲聊转闲聊分支、
  规则层闲聊不触发语义分类（行为不变）、流式 meta 正确；
- **"好的"关键词路由**（1 项）。

### 真实 API 冒烟测试（用户授权使用环境变量 Key）
- **L1 语义分类**（28 个样本）：推荐/事实/比较/闲聊 4 类全部正确且多数置信度 > 0.50；
  开放类 2 例被误分类但置信度低（0.284/0.403）→ 正确触发 L2；
- **L2 LLM 兜底**（7 个模糊/误分类样本）：6 例正确（含"好的吧"→chitchat，
  L1 误判 recommendation 被纠正），1 例"为什么司母戊鼎这么重"→factual（原因问题，可辩护）；
- **完整级联 end-to-end**（8 样本 + 2 流程）：清晰问题全走 semantic、模糊开放走 llm、
  闲聊走 L0 规则；`query("你好，你是谁")` 真实 LLM 回答 1.6s；KB 未构建时优雅报错。

## 配置说明（.env）

```bash
# 意图理解（L1 语义 + L2 LLM 兜底）
INTENT_SEMANTIC_ENABLED=true      # L1 语义意图分类总开关
INTENT_SEMANTIC_THRESHOLD=0.50    # L1 置信度阈值（余弦相似度，低于则走 L2 LLM 兜底）
INTENT_LLM_FALLBACK_ENABLED=true  # L2 LLM 兜底开关（仅低置信度时调用，按次计费）
```

## 服务器操作指引

1. 同步 `src/intent_classifier.py`（新增）、`src/rag_pipeline.py`、`src/config.py` 到服务器；
2. 重启 `python app.py --project jiabohui --host 0.0.0.0 --port 7860`；
3. 观察日志：`语义意图分类: xxx (置信度 x.xxx)`（L1）、`LLM 意图分类失败`（L2 异常时）；
   首次查询会预计算 37 个意图原型向量（约 3 批 Embedding 调用），之后命中缓存零成本；
4. 若某项目意图分类不准：调整 `INTENT_SEMANTIC_THRESHOLD`（调低更多走 L1、调高更多走 L2 兜底），
   或扩充 `SemanticIntentClassifier.INTENT_PROTOTYPES` 原型样本（领域相关样本可放入项目配置层，本次未实现）。

---

## 新增问题（第十轮补 - bug-113 首字延迟优化）

> 触发场景：用户反馈升级意图理解分层分类后，答案首字（TTFT）比之前久了很多。
> 实测定位：**首次查询时的意图原型向量计算阻塞 9.5s**（37 个原型逐个 `embed_query` 串行调用
> Embedding API，每次 ~250ms）+ 低置信度问题的 L2 LLM 串行调用（+~800ms）。
> 全量测试：`pytest tests/ -q` → **282 passed**（0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-113补 | 意图原型向量首算 9.5s 阻塞首次查询首字；原型向量存 exact_cache 有被 LRU 挤出重算风险 | `src/intent_classifier.py`、`src/rag_pipeline.py` | P1 | 已修复 |

## 问题详情

### [bug-113补] 意图原型向量计算阻塞首字（P1）

- **根因分析**：
  1. `_get_prototype_vectors` 对 37 个原型逐个调用 `embed_query`（单条 API 调用），**串行 37 次 ≈ 9.5s**；
     且仅懒加载、未预计算时该耗时发生在**首次查询线程内**（init_pipeline 的 warmup 未触发意图预计算，
     需知识库重建才会在 build 中预计算）→ 升级后未重建知识库的用户首次查询直接阻塞 9.5s；
  2. 原型向量写入 exact_cache（LRU 淘汰到 500 条），大量查询时可能被挤出 → 触发重算。
- **影响范围**：所有使用 L1 语义分类的查询；未重建知识库/重启后首次查询尤其明显（首字 9.5s+）。
- **修复方案**：
  1. `_get_prototype_vectors` 改用 **`embed_batch` 批量计算**（37 个原型 → 4 批并行，实测 **9.5s → 0.6s**），
     `embed_batch` 不可用/失败时回退逐个 `embed_query`（保持与测试 mock 兼容）；
  2. 原型向量改写入 **`pattern_cache`（set_pattern）**——该缓存不淘汰、持久化，重启后零 API 调用
     （实测重启 warmup 0.0s）；exact_cache 仅作读取回源；
  3. `RAGPipeline.warmup()` 增加 `intent_classifier.warmup()`（try/except 包裹）——启动时预计算原型，
     首查不再阻塞（实测 pipeline 构造 + warmup 0.7s）。
- **风险分析**：低。① embed_batch 是既有批量路径（含批大小钳制/维度校验/失败重试），行为一致；
  ② pattern_cache 校验逻辑（bug-037/067）与原型向量兼容（list[float]）；
  ③ warmup 失败仅告警不影响启动；④ 分类结果与阈值完全不变（**不牺牲准确率**）。
- **测试验证**：新增/更新 5 项测试（embed_batch mock 适配 ×4、warmup 预计算保护 ×2 测试文件）；
  真实 API 实测：冷启动原型预计算 9.5s→0.6s、重启后 0.0s、新问题 L1 classify 281ms（与检索共享单次
  embedding 调用，不额外计费）；完整 query_stream 首字：高置信 658~738ms、L0 闲聊 549ms（均与旧版持平）、
  低置信 L2 1689ms（其中 ~800ms 为 L2 LLM 串行，是"不牺牲准确率"的本质代价，检索在 L1 后缓存命中
  仅 ~10ms 无法并行隐藏，已确认无优化空间）；全量 282 passed。

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-113补 | 冷启动原型预计算 9.5s → 0.6s（embed_batch 批量）；重启 warmup 0.0s（pattern_cache 持久化命中）；重启后首查首字 658ms/1072ms（与旧版持平）；全量 282 passed | ✅ 已修复 |

### 实测数据（真实 API，Windows 本地）

| 场景 | 优化前 | 优化后 |
|------|--------|--------|
| 首次查询（原型未预计算） | 9.5s（原型计算阻塞） | 0.7s（启动 warmup 完成）+ 首字 ~660ms |
| 重启后 warmup | 9.5s（若懒加载） | 0.0s（pattern_cache 命中） |
| 高置信问题首字（缓存命中） | — | 658~738ms |
| 低置信问题首字（L2 兜底） | — | 1689ms（L2 ~800ms 为准确率代价） |
| L0 闲聊首字 | — | 549ms |

## 服务器操作指引

1. 同步 `src/intent_classifier.py`、`src/rag_pipeline.py` 到服务器；
2. 重启 `python app.py --project jiabohui --host 0.0.0.0 --port 7860`；
3. 启动日志出现 `意图原型向量就绪: 37 个原型 / 5 类`（warmup 预计算，~0.6s）即生效；
   首次查询不再有秒级延迟；原型向量持久化于 `data/processed/embedding_cache/pattern_cache.json`。

---

## 新增问题（第十一轮 - 输出答案去除 emoji）

> 需求：输出的答案中不要出现 emoji 表情和各种小图标（qwen 系列回答常带 😊🌟❤️ 等）。
> 全量测试：`pytest tests/ -q` → **308 passed**（原 282 + 新增 26，0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-114 | LLM 回答含 emoji/装饰图标；UI 检索来源/状态/按钮含图标符号 | `src/utils.py`, `src/llm.py`, `app.py`, `scripts/run_qa.py` | P2 | 已修复 |

## 问题详情

### [bug-114] 输出答案去除 emoji（P2）

- **根因分析**：qwen 系列模型回答习惯使用 emoji（😊🌟❤️✅ 等）装饰；Web UI 的检索来源（🟢🟡⚪ 相关度圆点、⏱ 响应时间、📚 检索来源标记）、状态提示（⚠️✅❌）、按钮/标题（🔄🗑️📊💡）也大量使用 emoji 图标 → 输出内容和界面出现"各种各样的表情和小图标"。
- **影响范围**：所有 LLM 回答（Web UI / CLI / SDK）、UI 展示的可读性与正式感。
- **修复方案**：
  1. **`src/utils.py` 新增 `strip_emoji(text)`**：Unicode 正则移除 emoji 表情/装饰图标（覆盖
     `\U0001F000-\U0001FFFF` 表情交通扩展、`\U00002600-\U000027BF` 杂项+装饰符号、
     `\U00002300-\U000023FF` 技术符号、`\U000025A0-\U000025FF` 几何形状、
     `\U00002196-\U00002199` 四角箭头、`\U00002B00-\U00002BFF` 杂项箭头、
     `\U0000FE00-\U0000FE0F` 变体符、`\U0000200D` ZWJ、`\U00003030` 波浪线）；
     **不误伤**中文标点/字母/数字/普通符号（© → 等保留）；
  2. **`src/llm.py` 三处输出点过滤**：`chat()` 新生成内容、缓存命中内容（兼容升级前旧缓存）、
     `chat_stream()` 逐 token（含 ZWJ/变体符残留）；
  3. **`app.py` 全部 14 处 UI emoji 替换为纯文本**：检索来源 `**📚 检索来源**`→`**[检索来源]**`、
     相关度圆点 🟢🟡⚪→`[高]/[中]/[低]`、⏱→去掉、⚠️✅❌⏳ 状态提示去图标、
     🔄🗑️📊💡 按钮/标题去图标、颜色图例同步改文本；
  4. **`scripts/run_qa.py`** CLI 问答界面全部去 emoji：表格标题 `📚 检索到的文物`→`检索到的文物`、
     标题 `🦁 文物知识库`→`文物知识库`、以及 `🔍问题/📊查询类型/💡回答/❌查询失败/⚠知识库未构建` 等提示全部去图标；
     范围说明：开发工具脚本（build_knowledge_base/generate_mock_data/generate_test_docs/benchmark_search）
     的统计输出装饰 emoji 不属于"问答答案"，本次保留（如需一并清理可后续处理）。
- **风险分析**：低。① `strip_emoji` 为纯文本后处理，不改变分类/检索/生成逻辑；
  ② 过滤范围经 26 项测试验证不误伤正常文本（中文标点/字母/数字/©→ 保留）；
  ③ `**[检索来源]**` 标记仅去 📚，`_convert_history` 截断逻辑（bug-034/104）不受影响；
  ④ 流式逐 token 过滤在 yield 前完成，输出全程无 emoji。
- **测试验证**：新增 `tests/test_emoji_filter.py`（26 项）：strip_emoji 参数化移除/保留用例、
  常见 emoji 全范围覆盖、正则无纯文本误匹配、chat 非流式过滤、chat 缓存命中过滤旧内容、
  chat_stream 逐 token 过滤、format_answer 无 emoji（含 RRF 路径）；同步更新
  `test_edge_cases.py`/`test_review_findings.py` 中 8 处 marker/圆点/⏱ 断言；
  真实 API：模型回答正常输出不受影响（qwen 本身倾向不用 emoji）；全量 308 passed。

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-114 | `TestStripEmoji`（16 项：emoji 移除 + 正常字符保留 + 全范围覆盖 + 无误匹配）；`TestLLMEmojiFilter`（3 项）；`TestFormatAnswerNoEmoji`（2 项）；旧断言 8 处同步更新；全量 308 passed | ✅ 已修复 |

## 服务器操作指引

1. 同步 `src/utils.py`、`src/llm.py`、`app.py`、`scripts/run_qa.py` 到服务器；
2. 重启 `python app.py --project jiabohui --host 0.0.0.0 --port 7860`；
3. 提问任意问题 → 回答不再出现 emoji；检索来源显示为 `**[检索来源]**` + `[高]/[中]/[低]` 文本标记；
4. 旧缓存中的回答（含 emoji）命中时会自动过滤，无需清缓存。

---

## 新增功能（第十二轮 - 答案文本清洗 TTS + 字幕展示）

> 需求：LLM 答案原始文本（含 Markdown/HTML/特殊符号/URL 等）在语音合成（TTS）与字幕展示时
> 会读出/显示语法噪音（`**`、`#`、`<div>`、`$x^2$` 等）。实现 `clean_text_for_tts()` 纯函数，
> 将答案清洗为适合 TTS + 字幕的纯文本，不改变内容含义与语气。
> 设计文档：`docs/superpowers/specs/2026-08-07-tts-clean-text-design.md`（已批准）
> 全量测试：`pytest tests/ -q` → **366 passed**（原 308 + 新增 58，0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-115 | 新增功能：答案原始文本清洗为 TTS + 字幕展示纯文本（删除 Markdown/HTML/特殊符号，保留正文与正常字符，规范化输出） | `src/utils.py`、`tests/test_tts_clean.py`（新增） | 功能增强 | 已实现 |

## 问题详情

### [bug-115] 答案文本清洗函数 `clean_text_for_tts`（功能增强）

- **需求确认**：
  1. 输入为答案原始文本（可能含 Markdown、HTML、特殊符号、URL 等）；
  2. 删除 Markdown 语法符号（标题/粗斜删除线/引用/代码块/行内代码/分隔线），仅保留正文；
  3. 删除 HTML 标签及属性，保留标签内文字；
  4. 删除 LaTeX 公式（简单转口语，复杂删除）、控制字符、零宽字符、制表符、emoji；
  5. 保留中文/英文标点、数字、%、货币（¥/$）、°C、版本号（v3.2）、商标符号；
  6. 输出规范化：段落合并（最多一个空行）、连续空格压缩为单个、每句结尾补标点。
- **实现方案**：
  1. `src/utils.py` 新增 `clean_text_for_tts(text: Optional[str]) -> str`（与 `strip_emoji` 同模块同风格）；
  2. **不接线** `llm.chat()/chat_stream()` 输出层：防幻觉检查 `verify_answer_grounding` 依赖回答中
     `**名称**` 标记提取名称，提前剥离会使其失效；全量改写回答格式属行为突变。作为独立工具函数
     供 TTS/字幕消费方按需调用；
  3. 清洗流水线（11 步）：统一换行 → 删代码块（``` 整块）→ 删 HTML（script/style 整体删、
     块级标签转段落、其余去标签留内文）→ 删 LaTeX（`$...$`/`\(...\)`，`x^2`→"x 的平方"、
     `\frac{a}{b}`→"b 分之 a"；复杂删除；`$` 后为数字判货币保留）→ 链接/图片语法留文字 →
     行内代码（命令/路径删除，普通词保留）→ 行级 Markdown（标题/引用/分隔线/表格/无序列表）→
     行内强调（~~ ** __ * _，下划线带字母边界保护不误伤 model_name）→ 数字区间波浪号转"到"
     （`3~5`→"3 到 5"）→ 特殊字符（emoji/控制/零宽/制表符）→ 规范化（空格压缩、段落≤1空行、
     列表序号补空格、句末补标点，标题行除外）。
- **关键决策**：
  1. 句末标点：行尾剥离 `，、：:` 后若不以 `。！？!?…．.;；` 结尾则补 `。`；标题行（原 `#` 标记）不补
     （示例输出 `退款流程` 无句号）；
  2. 货币与 LaTeX 区分：`$` 后紧跟数字视为货币保留，避免 `$5 和 $10` 误判为公式；
  3. 命令/路径启发式：多 token、命令关键字（pip/python/git/cd 等）、含 `/` 或 `\`、`--` 参数、
     `=` 赋值、已知文件扩展名 → 删除；单 token 普通词（`True`/`v3.2`）保留；
  4. 表格行 `|a|b|` → "a，b"（表格按块处理，分隔行删除不留空行）。
- **风险分析**：低。纯新增函数，不接线现有输出链路，不影响任何既有功能；格式化后处理不改语义；
  命令/路径删除为启发式，极端用例可能误删/误留，已用测试固定主流行为。
- **测试验证**：新增 `tests/test_tts_clean.py`（58 项）：用户验收示例（原样断言）、Markdown 全规则、
  HTML、LaTeX（含货币不误判）、特殊符号、保留字符、规范化、边界（空串/None/纯空白）。全部通过。

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-115 | 用户示例原样匹配；58 项单元测试通过；全量 366 passed；`py_compile` 语法检查通过；混合输入实测（标题/粗体/HTML/引用/列表/LaTeX/表格/链接/波浪号）输出正确 | ✅ 已实现 |

**全量测试**：`pytest tests/ -q` → **366 passed**（0 失败 0 错误）。

## 验证步骤（第十二轮）

### bug-115 验证
1. `python -c` 用户示例：`clean_text_for_tts('# 退款流程\n\n1. 在**订单页**点击"申请退款"\n2.金额将在3~5个工作日退回。温馨提示：请勿重复提交')`
   → `退款流程\n1. 在订单页点击"申请退款"。\n2. 金额将在 3 到 5 个工作日退回。温馨提示：请勿重复提交。`
2. `pytest tests/test_tts_clean.py -v` → 58 passed
3. `pytest tests/ -q` → 366 passed

### 使用方式（供 TTS/字幕消费方调用）
```python
from src.utils import clean_text_for_tts

clean = clean_text_for_tts(llm_answer)  # 答案原始文本 → TTS/字幕纯文本
```

---

## 新增变更（第十二轮补 - bug-115 业务接线：Web UI 展示层清洗）

> 背景：用户反馈"应直接和现有业务结合，前端页面提问即可验证"，而非仅独立工具函数。
> 接线点评估：`llm.chat()/chat_stream()` 输出层不可行（防幻觉检查 `verify_answer_grounding`
> 依赖回答中 `**名称**` 标记提取名称，提前剥离会使其失效、且全量改写回答格式属行为突变）；
> 选择 **app.py 展示层**（`answer_question`）——不影响防幻觉检查、不影响 LLM/检索缓存、
> 检索来源的 `**名称**` 加粗结构由 `format_answer` 保留。
> 全量测试：`pytest tests/ -q` → **369 passed**（0 失败 0 错误）

## 问题详情

### [bug-115 接线] `clean_text_for_tts` 接入 Web UI 问答链路（app.py）

- **根因分析**：函数仅独立提供时，用户在前端提问看到的仍是含 Markdown 噪音的原始回答，
  无法直接验证清洗效果；"适合 TTS + 字幕展示"的诉求需在真实业务链路上生效。
- **修复方案**（最小改动，3 处）：
  1. `app.py` 导入 `clean_text_for_tts`；
  2. 非流式分支：`display = format_answer(clean_text_for_tts(answer), chunks_info, timing)`；
  3. 流式分支：中间增量更新与最终完整更新均先 `clean_text_for_tts(full_answer)` 再 `format_answer`。
- **接线边界**（刻意保留）：
  1. 清洗仅作用于**答案正文本体**；`format_answer` 追加的 `**[检索来源]**` 标题与
     `**名称**` 加粗（UI 结构、非 LLM 生成内容）不清洗；
  2. 防幻觉检查（pipeline 内部，生成后立即执行）不受影响——它检查的是原始回答；
  3. LLM/检索缓存不受影响——缓存的是原始回答，清洗仅在展示层；
  4. 多轮对话：history 中存储的是清洗后展示文本，`_convert_history` 按 `**[检索来源]**`
     标记截断取答案正文，正文已无 Markdown，行为正常。
- **风险分析**：低。仅展示层后处理，不影响 pipeline/缓存/日志；流式中间态 token 不完整时
  可能存在半截标记（如单个 `*`），属瞬时显示、最终完整清洗后消失。
- **测试验证**：新增 `tests/test_tts_clean.py::TestAppIntegration`（3 项）：
  1. 非流式：mock pipeline 返回含 `#`/`**`/`3~5` 的答案 → history 展示文本已清洗（无标记、区间转"到"、正文保留）；
  2. 流式：逐 token 累积后最终展示已清洗；
  3. 检索来源结构保留：`**[检索来源]**` 与 `**司母戊鼎**` 加粗名称仍在（仅答案正文本体被清洗，
     断言 `**司母戊鼎**` 全文仅出现 1 次）。
  全部通过；全量 `pytest tests/ -q` → **369 passed**（原 366 + 新增 3）。

## 验证结果（第十二轮补）

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-115 接线 | `TestAppIntegration`（3 项）通过；全量 369 passed；既有 `answer_question` 相关测试（空问题/dict 历史/多轮 list content）不受影响 | ✅ 已接线 |

## 验证步骤（第十二轮补）

### 前端页面验证（用户可直接操作）
1. 启动 Web UI：`python app.py --project museum`（或独立部署命令）；
2. 提问一个含结构化回答的问题（如"推荐一些代表性的文物"），回答正文应无 `**`、`#`、
   emoji 等噪音，每句以标点结尾；检索来源区仍显示 `**[检索来源]**` 与加粗名称；
3. 流式/非流式两种模式均验证。

### 本地自动化验证
1. `pytest tests/test_tts_clean.py::TestAppIntegration -v` → 3 passed
2. `pytest tests/ -q` → 369 passed
