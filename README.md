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

### v1.3.3 (2024-08) — 当前版本

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

## 技术栈

| 环节 | 技术选型 | 版本 | 功能 | 部署方式 |
|------|---------|------|------|---------|
| **数据加载** | 自定义 `DataLoader` | v1 | 加载 JSON/CSV 数据，字段映射标准化 | 内置，零依赖 |
| **多格式文档解析** | pypdf / python-docx / python-pptx / PaddleOCR | 最新 | 解析 PDF、Word、PPT、图片(OCR) 等格式 | 本地（GPU 可选） |
| **文档切片** | 自定义 `SmartChunking` | v2 | 每项生成 3 个切片（summary/detail/significance），信息密度高、重叠少 | 内置 |
| **Embedding 生成** | 阿里云百炼 `text-embedding-v3` | v3 | 1024 维向量，中文语义理解优秀，批处理并发 | **在线 API** |
| **Embedding 缓存** | 自定义 `EmbeddingCache` | v2 | 高频问题预计算 + 精确匹配 + 模式匹配，持久化到磁盘 | 内置 |
| **向量数据库** | **Qdrant**（本地模式） | ≥1.9 | 本地持久化，零配置；支持本地持久模式（查询快） | 本地 |
| **关键词检索** | **rank-bm25**（BM25Okapi） | ≥0.2 | 中文 unigram 分词，与语义检索互补 | 内置（内存索引） |
| **混合检索融合** | 自定义 `HybridRetriever` | v2 | 语义检索 + BM25 并行执行，RRF 算法融合排序，去重 | 内置 |
| **重排序** | 百炼 **`qwen3-reranker-4b`**（默认）/ `qwen3-reranker-8b` / 本地 TF-IDF（降级） | — | 对检索结果精排 | 在线 API / 本地 |
| **LLM 问答** | 阿里云百炼 **`qwen-plus`**（默认）/ `qwen-max` | 3.7+ | 日常问答用 qwen-plus，复杂推理用 qwen-max | **在线 API** |
| **LLM 缓存** | 自定义 `LRUCache` | v1 | 相同问题不重复调用 API，TTL 30 分钟 | 内置 |
| **查询分类** | 自定义 `classify_query` | v2 | 基于评分机制，15+ 种模式，识别推荐/事实/比较/开放/闲聊 | 内置 |
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

使用百炼 `text-embedding-v3` 模型，每批 16 条并发，自动重试，缓存持久化。

### 4. 数据入库

- **向量数据库**：Qdrant 本地持久化（`data/processed/{project_id}/qdrant_db/`）
- **BM25 索引**：内存中构建，用于关键词检索
- **切片缓存**：JSON 格式保存（`data/processed/{project_id}/chunks.json`）

---

## RAG 问答流程

### 完整处理流程

以用户提问 **"推荐一些代表性的文物"** 为例：

```
Step 1: 查询分类
────────────────────────────────────────────────────────────
输入: "推荐一些代表性的文物"
输出: QueryType.RECOMMENDATION
逻辑: 命中关键词 ["推荐", "代表性"]

Step 2: 混合检索（并行）
────────────────────────────────────────────────────────────
a) 语义检索（向量）:
   用户问题 → text-embedding-v3 → 1024维向量
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
| **图片** | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`, `.tiff` | PaddleOCR (GPU) / Tesseract | OCR 文字识别 |

### 使用示例

```bash
# 从文档目录构建知识库（指定项目）
python scripts/build_knowledge_base.py --project museum --source docs --doc-path ./data/raw/museum/docs

# 混合模式（JSON 数据 + 文档）
python scripts/build_knowledge_base.py --project enterprise --source mixed

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
A: 百炼 API 按量计费，qwen-plus 约 0.004元/千 tokens，text-embedding-v3 约 0.0007元/千 tokens。日常使用费用很低。

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

---

## 许可证

本项目仅供学习和研究使用。