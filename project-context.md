# 项目上下文快照

> 生成时间: 2024-01 (最后更新: 2024-08)
> 项目根目录: `E:/project/agent_project/pi/test/`

---

## 1. 项目概述

### 名称
**文化知识库 RAG 问答系统** — 基于检索增强生成（Retrieval-Augmented Generation）的知识库问答系统，支持多项目架构。

### 版本
当前版本: **v1.3.4** (2024-08)

### 技术栈

| 环节 | 技术选型 | 部署方式 |
|------|---------|---------|
| 数据加载 | 自定义 `DataLoader` | 内置 |
| Excel 数据 | openpyxl（.xlsx） | 内置（可选依赖） |
| 文档解析 | pypdf / python-docx / python-pptx / PaddleOCR | 本地 |
| 文档切片 | 自定义 `SmartChunking v2`（3 切片: summary/detail/significance） | 内置 |
| Embedding | 阿里云百炼 `text-embedding-v4`（1024 维，bug-110 已从 v3 升级） | **在线 API** |
| 向量数据库 | **Qdrant**（本地持久化 / 全内存模式） | 本地 |
| 关键词检索 | **rank-bm25**（BM25Okapi，中文 unigram） | 内置（内存索引） |
| 混合检索 | 自定义 `HybridRetriever`（并行语义 + BM25，RRF 融合，去重） | 内置 |
| 重排序 | 百炼 **`qwen3-reranker-4b`**（默认）/ `qwen3-reranker-8b` / 本地 TF-IDF（降级） | 在线 API / 本地 |
| LLM | 阿里云百炼 **`qwen-plus`**（默认）/ `qwen-max` | **在线 API** |
| 缓存 | 三层 LRU（Embedding 持久化 + LLM 响应 + 检索结果） | 内置 |
| 查询分类 | 评分机制，15+ 种模式 → 推荐/事实/比较/开放/闲聊 | 内置 |
| 闲聊路由 | 关键词匹配，非知识库问题跳过 RAG | 内置 |
| 上下文裁剪 | 按段落裁剪，上限 10000 字符 | 内置 |
| 多轮对话 | 保留最近 4 轮（8 条消息） | 内置 |
| 回答质量评估 | `verify_answer_grounding()` 防幻觉检查 | 内置 |
| 项目管理 | 自定义 `ProjectManager`（内置 + 外部 JSON 配置） | 内置 |
| Web UI | **Gradio** ≥4.44 | 本地 |
| 配置管理 | **Pydantic Settings** ≥2.1（.env + 环境变量） | 内置 |
| 日志 | **Loguru** ≥0.7（彩色控制台 + 文件轮转 30 天） | 内置 |
| 包管理 | Conda（environment.yml） / pip（requirements.txt） | 系统 |

### 项目架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                        用户接口层                                     │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────────┐  │
│  │ Gradio Web UI│  │ 交互式 CLI      │  │ Python API (SDK)        │  │
│  │ (app.py)     │  │ (run_qa.py)    │  │ (RAGPipeline 类)        │  │
│  └──────┬───────┘  └───────┬────────┘  └───────────┬─────────────┘  │
└─────────┼──────────────────┼────────────────────────┼────────────────┘
          │                  │                        │
┌─────────▼──────────────────▼────────────────────────▼────────────────┐
│                      RAG 流水线 (rag_pipeline.py)                    │
│                                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │ 查询分类  │→ │ 混合检索  │→ │ 重排序   │→ │ Prompt构建 + LLM    │  │
│  │ classify │  │ hybrid   │  │ reranker │  │ generation           │  │
│  └──────────┘  └────┬─────┘  └──────────┘  └──────────────────────┘  │
└──────────────────────┼───────────────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────────────┐
│                      ProjectManager                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│  │ 博物馆项目    │  │ 企业项目      │  │ 自定义项目    │  ...          │
│  │ museum       │  │ enterprise   │  │ custom       │                │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                │
│         │                 │                 │                         │
│         ▼                 ▼                 ▼                         │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐                    │
│  │ Qdrant   │      │ Qdrant   │      │ Qdrant   │  ← 独立集合        │
│  │ museum   │      │ enterpr. │      │ custom   │                    │
│  ├──────────┤      ├──────────┤      ├──────────┤                    │
│  │ BM25 idx │      │ BM25 idx │      │ BM25 idx │  ← 独立索引        │
│  ├──────────┤      ├──────────┤      ├──────────┤                    │
│  │ Prompt   │      │ Prompt   │      │ Prompt   │  ← 独立Prompt      │
│  └──────────┘      └──────────┘      └──────────┘                    │
└──────────────────────────────────────────────────────────────────────┘
```

### 数据流

```
用户输入 → 路由判断(is_kb_related)
  ├── 闲聊/非知识库 → 直接 LLM (qwen-plus) ← 无检索，最快
  └── 知识库相关 → 进入 RAG 流水线
                    │
                    ▼
        查询分类(classify_query) → 推荐/事实/比较/开放
                    │
                    ▼
        混合检索(并行) ─┬─ 语义: Embedding → Qdrant
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

## 2. 已完成的核心功能

### 模块清单

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 配置管理 | `src/config.py` | ✅ 完成 | Pydantic Settings，.env + 环境变量，类型校验 |
| 工具函数 | `src/utils.py` | ✅ 完成 | 日志、JSON 读写、ID 生成 |
| 数据加载 | `src/data_loader.py` | ✅ 完成 | JSON/CSV/Excel(.xlsx) 加载，字段映射标准化，任意列可检索（bug-109） |
| 文档加载 | `src/document_loader.py` | ✅ 完成 | PDF/Word/TXT/MD/图片(OCR) |
| 切片策略 | `src/chunking.py` | ✅ 完成 | SmartChunking v2，3 切片 |
| Embedding | `src/embeddings.py` | ✅ 完成 | 百炼 API，批处理，缓存 |
| 向量数据库 | `src/vector_store.py` | ✅ 完成 | Qdrant，本地/内存/远程模式 |
| 关键词检索 | `src/retriever.py` (BM25Retriever) | ✅ 完成 | BM25Okapi，中文 unigram 分词 |
| 混合检索 | `src/retriever.py` (HybridRetriever) | ✅ 完成 | 并行语义+BM25，RRF 融合，去重 |
| 重排序 | `src/reranker.py` | ✅ 完成 | Qwen3-Reranker API + TF-IDF fallback |
| LLM | `src/llm.py` | ✅ 完成 | 百炼 Qwen API，流式/非流式 |
| 缓存 | `src/cache.py` | ✅ 完成 | 三层 LRU（Embedding/LLM/检索） |
| 项目管理 | `src/project.py` | ✅ 完成 | 多项目隔离，内置 + JSON 配置 |
| RAG 流水线 | `src/rag_pipeline.py` | ✅ 完成 | 核心编排，全部功能集成 |
| Web UI | `app.py` | ✅ 完成 | Gradio，项目选择器，检索可视化 |
| 构建脚本 | `scripts/build_knowledge_base.py` | ✅ 完成 | 支持 --project 参数 |
| CLI 工具 | `scripts/run_qa.py` | ✅ 完成 | 交互式/单次查询 |
| Mock 数据 | `scripts/generate_mock_project_data.py` | ✅ 完成 | 双项目 Mock 数据生成 |
| 测试 | `tests/test_pipeline.py` | ✅ 完成 | 流水线基础测试 |

### 关键功能特性

1. **多项目架构**：每个项目独立 Qdrant 集合 + BM25 索引 + Prompt 模板 + 数据目录
2. **代码泛化**：不包含任何领域特定关键词（如"文物""鼎""剑"等），适用于任意领域
3. **闲聊路由**：`is_kb_related()` 基于通用闲聊模式匹配，非知识库问题直接 LLM 回答
4. **查询分类**：基于评分机制，15+ 种模式，自动识别推荐/事实/比较/开放五类
5. **混合检索**：语义 + BM25 并行执行，RRF 融合，按文物去重
6. **三层缓存**：Embedding 持久化缓存（1000+ 条）+ LLM 响应缓存（256 条，30min TTL）+ 检索结果缓存（128 条，5min TTL）
7. **上下文裁剪**：保留完整段落，上限 10000 字符，按相关性（检索顺序）保留
8. **多轮对话**：保留最近 4 轮对话，支持追问
9. **回答质量评估**：`verify_answer_grounding()` 检查 LLM 回答是否基于检索上下文
10. **Qdrant 全内存模式**：`QDRANT_MEMORY_MODE=true` 时全部向量加载到 RAM，检索 < 5ms

---

## 3. 设计决策记录

### 架构决策

| 决策项 | 选择 | 理由 |
|-------|------|------|
| 项目切换方式 | 下拉菜单 + API 参数 `project_id` | 用户直接在 UI 切换或通过参数指定，无需重启 |
| BM25 隔离 | 每个项目独立 BM25 索引 | 数据完全隔离，互不干扰 |
| Qdrant 策略 | 每项目独立集合（`project_xxx`） | 逻辑隔离，Qdrant 原生支持多集合 |
| 配置格式 | JSON 文件 | 简单、可读、无需数据库 |
| 内置项目 | museum + enterprise | 覆盖两种典型场景（文化/商业） |
| 部署方式 | 每个项目独立进程 | 完全进程隔离，独立端口，可独立扩缩容 |

### 模型选择

| 决策项 | 选择 | 理由 |
|-------|------|------|
| 默认 LLM | `qwen-plus` | 速度快 2 倍，成本低 10 倍，足以胜任 RAG 任务 |
| 备用 LLM | `qwen-max` | 复杂推理场景（长期规划、多步推理） |
| 重排序模型 | `qwen3-reranker-4b` | 替代已下线的 gte-rerank，性价比均衡 |
| 高精度重排序 | `qwen3-reranker-8b` | 更准但稍慢，通过配置项可选 |
| Embedding 模型 | `text-embedding-v4` | 1024 维，中文语义理解优秀（bug-110 已从 v3 升级） |

### 关键业务规则

1. **推荐类问题**：从检索结果中挑选 3~5 个最有代表性的，覆盖不同类型
2. **事实类问题**：基于参考信息回答，不编造；信息不足时如实说明
3. **闲聊路由**：问候、告别、感谢、自我介绍等直接 LLM 回答，不走 RAG
4. **重排序条件**：检索结果 ≤ 3 条时跳过重排序，节省 ~200ms
5. **上下文裁剪**：按段落整体裁剪，不截断段落；优先保留靠前的段落（相关性最高）
6. **多轮对话**：保留最近 4 轮（8 条消息），超出则丢弃最早的
7. **缓存 TTL**：LLM 响应 30 分钟，检索结果 5 分钟，Embedding 持久化

### 命名规范

- **Python 文件**：小写蛇形命名（`rag_pipeline.py`）
- **类名**：PascalCase（`RAGPipeline`, `ProjectManager`）
- **函数/方法**：小写蛇形（`is_kb_related`, `build_knowledge_base`）
- **常量**：大写蛇形（`MAX_CONTEXT_CHARS`, `CHITCHAT_KEYWORDS`）
- **项目 ID**：小写字母（`museum`, `enterprise`）
- **Qdrant 集合名**：`project_{project_id}`（如 `project_museum`）

---

## 4. 当前开发进度

### 当前状态
- ✅ **v1.3.4 已完成发布**（第八轮生产环境修复：bug-095 ~ bug-100）
- ✅ **v1.3.5-pre（开发中）新增功能与修复**：Excel 数据源（bug-109）、Embedding v4 升级（bug-110）、
  Web UI 下拉框误切换（bug-111）、推荐 prompt 相关性/品类过滤（bug-112）
- ✅ **112 个已识别问题已全部处理**（bug-001 至 bug-108 修复；bug-109~112 为第九轮新增功能/修复；
  bug-032 编号不存在；bug-094 标注需确认，详见 bug-fix-plan.md）
- ✅ **243 项单元测试全部通过**（0 失败、0 错误）
- ✅ **代码已通过语法检查**（18 个 Python 源文件）
- ⏳ **服务器部署中**：jiabohui（家博会）项目已构建并启动 Web UI（端口 7860），
  运行中待办见下方「服务器运行状态与待办」章节

### Bug 修复总览

| 修复轮次 | 覆盖范围 | 涉及文件 | 状态 |
|---------|---------|---------|------|
| 第一轮 | bug-001 ~ bug-005 | `llm.py`, `rag_pipeline.py`, `retriever.py`, `app.py` | ✅ 已完成 |
| 第二轮 | bug-006 ~ bug-011 | `cache.py`, `rag_pipeline.py`, `vector_store.py`, `app.py` | ✅ 已完成 |
| 第三轮 | bug-012 ~ bug-028 | 多文件（cache, app, rag_pipeline, data_loader, retriever, embeddings, document_loader 等） | ✅ 已完成 |
| 第四轮 | bug-029 ~ bug-033 | `build_knowledge_base.py`, `app.py`, `rag_pipeline.py` | ✅ 已完成 |
| 第五轮（测试工程师） | bug-034 ~ bug-053 | `rag_pipeline.py`, `cache.py`, `app.py`, `vector_store.py`, `retriever.py` 等 | ✅ 已完成 |
| 第六轮（独立审查） | bug-054 ~ bug-061 | `app.py`, `src/reranker.py`, `src/project.py`, `src/rag_pipeline.py`, `src/document_loader.py`, `src/chunking.py`, `src/data_loader.py` | ✅ 已完成 |
| 第七轮（独立审查） | bug-089 ~ bug-093（+bug-094 需确认） | `src/rag_pipeline.py`, `src/chunking.py`, `app.py` | ✅ 已完成（5 修复 + 1 待确认） |
| 第八轮（生产环境） | bug-095 ~ bug-108 | `src/embeddings.py`, `src/llm.py`, `src/reranker.py`, `src/utils.py`, `src/config.py`, `src/rag_pipeline.py`, `src/chunking.py`, `src/project.py`, `app.py`, `requirements.txt` | ✅ 已完成 |
| 第九轮（v1.3.5-pre） | 功能：bug-109（Excel 数据源）、bug-110（Embedding v4 升级）；修复：bug-111（UI 下拉框）、bug-112（推荐 prompt 过滤，含根因更正） | `src/data_loader.py`, `src/document_loader.py`, `src/config.py`, `src/embeddings.py`, `app.py`, `src/rag_pipeline.py`, `src/project.py`, `requirements.txt` | ✅ 已完成 |

> 注：bug-032 编号不存在（历史记录中从 bug-031 直接到 bug-033）。

### 待完成的任务

1. **功能测试**
   - [x] 测试博物馆项目构建 + 问答（服务器实测：构建成功 38 切片、run_qa 查询正常）
   - [x] 测试企业项目构建 + 问答（待用户补充完整验证）
   - [x] 测试 Web UI 启动（服务器实测：Gradio 6 兼容修复后正常启动并可访问）
   - [ ] 测试独立部署（两个端口同时运行）
   - [x] 测试闲聊路由正确性（复合闲聊句误判已修复，bug-093/097）
   - [ ] 测试多轮对话连贯性
   - [ ] jiabohui：重新构建知识库后验证 v4 向量检索质量（用户已执行 v4 验证 OK，重建待确认）
   - [ ] jiabohui：recommend prompt 品类匹配指令已改（代码层），服务器 `data/projects/jiabohui.json` 的
         recommend 模板待手动同步（详见 bug-112 操作指引）
   - [ ] 推荐混入不相关项问题：验证 prompt 过滤生效（问"我要买沙发，推荐几个展位给我"）

2. **文档完善**
   - [x] README 中多项目架构部分已更新
   - [x] project-context.md 已生成（本文件）
   - [x] bug-fix-plan.md 已更新（覆盖 bug-001 ~ bug-112）
   - [x] README/DEPLOY_GUIDE 中 `--project`/`--no-stream` 已与代码一致（bug-054）
   - [x] README 版本日志已固化至 v1.3.4（第八轮生产环境修复）
   - [x] requirements.txt 已固化 Gradio/Starlette/FastAPI 配套版本约束（bug-099/100）
   - [x] README 已记录 Excel 数据源（bug-109）与 Embedding v4（bug-110）

3. **潜在的改进方向**
   - [ ] 添加更多内置项目模板（如法律、医疗、教育）
   - [ ] 项目配置热加载（无需重启）
   - [ ] 添加项目级访问控制
   - [ ] 统一 API 接口（FastAPI 封装）
   - [ ] 添加 WebSocket 流式支持

---

## 5. 已知问题与约束

### 已知 Bug

所有已识别的 99 个 Bug 已全部修复（bug-094 标注需确认，详见 bug-fix-plan.md）：
- bug-001 ~ bug-005：第一轮修复（核心运行时、RAG 路由、类型标注、检索逻辑、UI）
- bug-006 ~ bug-011：第二轮修复（缓存模式匹配、LRU 淘汰、竞态条件、闲聊误判、哈希冲突、空值比较）
- bug-012 ~ bug-028：第三轮修复（缓存恢复、线程安全、消息序列、查询分类、数据加载、分词、防幻觉、流式UI等）
- bug-029 ~ bug-033：第四轮修复（构建路径、消息格式、分隔符、memory_mode 路径检查等）
- bug-034 ~ bug-053：第五轮修复（测试工程师发现：消息序列、知识库加载、缓存格式校验、竞态、空值崩溃、路径遍历等）
- bug-054 ~ bug-061：第六轮修复（独立审查发现：CLI 参数缺失、Reranker API 契约、Prompt 花括号、闲聊误判、OCR 版本兼容、资源释放、tags 类型、ID 碰撞）
- bug-062 ~ bug-072：第五/六轮复测修复（检索缓存隔离、API 退避重试、chitchat/配置接线、增量添加缓存、Qdrant 重连、并发预热竞态等）
- bug-080 ~ bug-088：第七轮复测修复（陈旧向量清理、重复切片去重、qdrant query_points 兼容、Embedding 维度校验、LLM 缓存 key 补齐、分数阈值自适应、长文档切段、close 竞态）
- bug-089 ~ bug-093：第七轮独立审查（reranker_model 接线、tags 数字列表切片崩溃、防幻觉跨行正则、Web UI 配置接线、闲聊复合句误判）
- bug-095 ~ bug-108：第八轮生产环境（API 4xx 快速失败+错误详情、embedding 批大小钳制、防幻觉字段标签误报、Gradio 6 构造参数/消息格式/多模态 content 兼容、Starlette 版本约束、dashscope 流式合并模式内容膨胀修复、模型知识截止日期声明、按需自动联网搜索、qdrant-client 1.10+ 结构变更兼容、Gradio 6 emoji 头像无效路径崩溃）

### 技术债务

1. **~~`scripts/run_qa.py` 未支持 `--project`~~**（已解决：bug-054 修复时同步补齐 `--project` 参数，`run_qa.py` 支持项目隔离与项目专属缓存路径）
2. **`scripts/generate_mock_data.py`**（旧版单项目生成器）仍保留但未使用，可考虑删除
3. **`data/raw/artifacts.json`**（旧版单项目数据）仍保留但未使用
4. **`remaining-issues.md`** 中列出的 6 项未修复问题（R02/R09/R23/R26 及遗留项）仍待评估（不影响功能的代码质量问题）

### 约束

1. **API 依赖**：完全依赖阿里云百炼在线 API，离线无法使用
2. **首次构建慢**：首次构建知识库需要调用 Embedding API 批处理，约 30-60 秒
3. **Qdrant 内存模式**：全内存模式下重启后需要重新构建知识库（除非使用持久化快照）
4. **BM25 仅限内存**：BM25 索引全部在内存中，大项目（>10 万文档）可能消耗较多内存
5. **并行检索线程数**：`max_workers=2` 固定，不可配置

---

## 6. 关键代码约定

### API 设计规范

**RAGPipeline 类**（核心对外接口）：

```python
pipeline = RAGPipeline(
    project_id="museum",         # 项目 ID，可选
    local_mode=True,             # Qdrant 本地模式
    enable_cache=True,           # 启用缓存
    memory_mode=False,           # Qdrant 全内存模式
)

# 构建知识库
stats = pipeline.build_knowledge_base(
    data_path="data/raw/museum/data.json",
    overwrite=True,
)

# 非流式查询
result = pipeline.query(
    question="推荐一些代表性的文物",
    top_k=10,
    rerank=True,
    conversation_history=conversation_history,  # 多轮对话
)
# result 字段: answer, query_type, retrieved_chunks, context, timing, from_kb

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

**ProjectManager**（项目管理）：

```python
from src.project import project_manager

# 切换项目
cfg = project_manager.switch_to("museum")
print(cfg.name)          # "博物馆知识库"
print(cfg.collection_name)  # "project_museum"

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

**build_knowledge_base.py**：

```bash
# 构建指定项目
python scripts/build_knowledge_base.py --project museum --source json

# 列表项目
python scripts/build_knowledge_base.py --list-projects
```

### 错误处理规范

1. **API 调用失败**：自动重试 3 次，指数退避
2. **重排序 API 失败**：自动降级到本地 TF-IDF
3. **Qdrant 远程连接失败**：自动回退到本地模式
4. **Embedding 失败**：重试 3 次后抛出 `RuntimeError`
5. **LLM 失败**：重试 3 次后抛出 `RuntimeError`
6. **知识库未构建**：`query()` 和 `query_stream()` 抛出 `RuntimeError`

### 缓存键生成

```python
# Embedding 缓存：精确问题文本 + 模式匹配
cache_key = question  # 精确匹配
pattern_match(pattern, question)  # 模式匹配

# LLM 响应缓存：model + messages + temperature 的 MD5
cache_key = hashlib.md5(f"{model}:{messages}:{temperature}".encode()).hexdigest()

# 检索结果缓存：query + top_k + filter_conditions 的字符串键
cache_key = f"retrieve:{query}:{top_k}:{filter_str}"
```

### Prompt 模板约定

每个项目可以自定义 4 种 Prompt 模板：
- `recommend`：推荐类问题（挑选 3~5 个最有代表性的）
- `factual`：事实类问题（基于参考信息精准回答）
- `default`：默认模板（开放讨论等）
- `chitchat`：闲聊模式（直接 LLM 回答）

模板中 `{context}` 会被替换为检索到的上下文。

---

## 7. 测试与部署

### 测试策略

当前测试状态：
- **语法检查**：所有 16 个 Python 文件通过 `py_compile` 语法检查
- **单元测试**：`tests/test_pipeline.py` 包含基础流水线测试
- **集成测试**：暂无自动集成测试

建议的测试流程：
1. 生成 Mock 数据 → `python scripts/generate_mock_project_data.py`
2. 构建博物馆知识库 → `python scripts/build_knowledge_base.py --project museum --source json`
3. 构建企业知识库 → `python scripts/build_knowledge_base.py --project enterprise --source json`
4. 启动 Web UI 测试 → `python app.py --project museum`
5. 启动第二个服务测试 → `python app.py --project enterprise --port 7861`

### 部署环境

**开发环境（Windows）**：
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python scripts/generate_mock_project_data.py
python scripts/build_knowledge_base.py --project museum --source json
python app.py --project museum
```

**生产环境（GPU 服务器 Linux）**：
```bash
conda env create -f environment.yml
conda activate cultural-relics-rag
bash setup_gpu.sh
# 或手动部署
python scripts/generate_mock_project_data.py
python scripts/build_knowledge_base.py --project museum --source json
python app.py --project museum --host 0.0.0.0 --port 7860
```

**独立部署多个项目**：
```bash
# 终端1：博物馆 (端口 7860)
python app.py --project museum --port 7860

# 终端2：企业 (端口 7861)
python app.py --project enterprise --port 7861

# 终端3：自定义 (端口 7862)
python app.py --project custom --port 7862
```

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DASHSCOPE_API_KEY` | (必填) | 阿里云百炼 API Key |
| `EMBEDDING_MODEL_NAME` | `text-embedding-v4` | Embedding 模型 |
| `EMBEDDING_DIMENSION` | `1024` | 向量维度 |
| `LLM_MODEL_NAME` | `qwen-plus` | LLM 模型 |
| `LLM_TEMPERATURE` | `0.7` | 生成温度 |
| `QDRANT_HOST` | `localhost` | Qdrant 主机 |
| `QDRANT_PORT` | `6333` | Qdrant 端口 |
| `QDRANT_MEMORY_MODE` | `false` | 全内存模式 |
| `RERANKER_MODEL` | `qwen3-reranker-4b` | 重排序模型 |
| `RERANKER_ENABLED` | `true` | 是否启用重排序 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

### 数据目录结构

```
data/
├── projects/                    # 项目配置 (JSON)
│   ├── museum.json
│   └── enterprise.json
├── raw/
│   ├── museum/                  # 博物馆项目原始数据
│   │   └── data.json (17条)
│   ├── enterprise/              # 企业项目原始数据
│   │   └── data.json (14条)
│   ├── artifacts.json (旧)
│   └── docs/                    # 多格式测试文档
└── processed/                   # 处理后数据 (按项目自动生成)
    ├── museum/
    │   ├── chunks.json
    │   └── qdrant_db/
    └── enterprise/
        ├── chunks.json
        └── qdrant_db/
```

---

## 附录：关键文件快速参考

| 文件 | 核心类/函数 | 行数 | 职责 |
|------|------------|------|------|
| `src/project.py` | `ProjectConfig`, `ProjectManager` | ~200 | 项目配置管理，多项目隔离 |
| `src/rag_pipeline.py` | `RAGPipeline` | ~500 | 核心 RAG 流水线编排 |
| `src/vector_store.py` | `VectorStore` | ~220 | Qdrant 向量数据库封装 |
| `src/retriever.py` | `BM25Retriever`, `HybridRetriever` | ~250 | 混合检索（语义 + BM25） |
| `src/cache.py` | `LRUCache`, `EmbeddingCache` | ~300 | 三层 LRU 缓存系统 |
| `src/embeddings.py` | `BailianEmbedding` | ~150 | 百炼 Embedding API 封装 |
| `src/llm.py` | `BailianLLM` | ~150 | 百炼 LLM API 封装 |
| `src/reranker.py` | `BailianReranker` | ~150 | 重排序 + TF-IDF fallback |
| `src/chunking.py` | `SmartChunking`, `ChunkingPipeline` | ~200 | 智能切片策略 |
| `src/config.py` | `Settings` | ~120 | Pydantic 配置管理 |
| `src/data_loader.py` | `DataLoader`, `Artifact` | ~250 | 数据加载与标准化（JSON/CSV/Excel） |
| `src/utils.py` | `setup_logger`, `load_json`, `save_json` | ~80 | 工具函数 |
| `app.py` | `create_ui`, `answer_question` | ~250 | Gradio Web UI |
| `scripts/build_knowledge_base.py` | `main` | ~100 | 知识库构建脚本 |
---

## 服务器运行状态与待办（2026-08-07 快照）

> 本节记录服务器（ub-server）部署现场，供新会话续接，避免重复排查。

### 服务器环境
- 路径：`/data/codes/rag_chat`（conda 环境：`/data/conda_envs/cultural-relics-rag`）
- 项目：jiabohui（家博会数字人小虎，外部项目 `data/projects/jiabohui.json`，自定义 prompts）
- Web UI：`python app.py --project jiabohui --host 0.0.0.0 --port 7860`

### 已确认的服务器事实（勿重复排查）
1. **Embedding 已升级 v4 并验证**：`TextEmbedding.call(model='text-embedding-v4', input='测试', api_key=settings.dashscope_api_key)` 返回 200；
   `settings.embedding_model_name == 'text-embedding-v4'`（需同步新版 `src/config.py`/`src/embeddings.py` 到服务器后生效）。
2. **重排 API 生效（非 TF-IDF）**：`.env` 配 `RERANKER_MODEL=qwen3-rerank`（注意控制台模型名，
   非 qwen3-reranker-4b），日志 `Qwen3-Reranker 重排序完成: 10 → 5 条` 17+ 次 = 控制台 17 次成功调用。
3. **知识库**：jiabohui `data/processed/jiabohui/chunks.json` 171 切片已构建；
   v4 向量重建状态**待确认**（用户执行过 v4 验证，但重建后是否重新构建知识库未最终确认）。
4. **429 限流**：构建时偶发 `429 - Allocated quota exceeded`（账号 TPM 配额低），重试机制自动恢复，
   非代码缺陷；缓解：控制台提升配额 / 降低并发 / 加长退避（未实施，用户未要求）。

### 服务器待办（下一步）
1. **同步最新代码**（本地已提交，服务器未同步）：`src/config.py`、`src/embeddings.py`、
   `src/data_loader.py`、`src/document_loader.py`、`src/rag_pipeline.py`、`src/project.py`、
   `app.py`、`requirements.txt`（含 openpyxl 依赖）。
2. **`.env` 检查**：`EMBEDDING_MODEL_NAME`（v4，注意旧拼写 `EMBEDDING_MOD_NAME` 不生效）、
   `RERANKER_MODEL=qwen3-rerank`（保持）。
3. **清 v3 缓存**：`rm -rf data/processed/embedding_cache`（v3 查询缓存需清，防新旧向量混用）。
4. **重新构建知识库**（v4 向量）：`python scripts/build_knowledge_base.py --project jiabohui --source docs --doc-path ./data/raw/jiabohui/docs --category "家博会资料"`。
5. **`data/projects/jiabohui.json` 手动改 recommend 模板**：加品类匹配指令
   （"**相关性优先**：只推荐与用户问题**直接相关**的项；若参考信息中标明了**品类/类型/类别**，
   优先推荐与用户需求品类匹配的项，品类明显不匹配的**不要推荐**（宁缺毋滥，不要为凑满数量硬推）"）。
6. **重启服务**并验证：下拉框默认选中 jiabohui（bug-111 修复）、问"你是谁"返回小虎人设、
   问"我要买沙发，推荐几个展位给我"不再推设计品牌（bug-112 prompt 生效）。
7. **可选**：开通 qwen3-reranker-4b 或保持 qwen3-rerank（不阻塞）。
