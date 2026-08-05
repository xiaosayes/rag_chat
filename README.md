# 文物知识库 RAG 问答系统

> 基于阿里云百炼（DashScope）API 的端到端文物知识库检索增强生成（RAG）系统。
> 支持文物推荐、事实查询、比较分析等多种问答类型。

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
- [推荐类问题处理详解](#推荐类问题处理详解)
- [多格式文档支持](#多格式文档支持)
- [API 参考](#api-参考)
- [多项目架构](#多项目架构)
- [Conda 环境部署（GPU 服务器）](#conda-环境部署gpu-服务器)
- [部署指南](#部署指南)
- [性能优化](#性能优化)
- [Web UI 问答界面](#web-ui-问答界面)
- [项目结构](#项目结构)
- [常见问题](#常见问题)

---

## 更新日志

### v1.3.0 (2024-01) - 当前版本

#### 新增功能
- **多项目架构**：新增 `ProjectManager` 项目管理模块，支持多项目独立配置、独立 Qdrant 集合、独立 BM25 索引、独立 Prompt 模板
- **内置项目**：博物馆（museum）+ 企业（enterprise）两个内置项目，一键切换
- **下拉菜单切换**：Web UI 新增项目选择器，切换项目自动重建 Pipeline
- **独立部署**：每个项目可独立启动服务实例（不同端口），完全进程隔离

#### 代码泛化性提升
- **Prompt 模板**：从"中国文物专家"改为"知识助手"，不绑定任何领域
- **`is_kb_related()`**：移除领域特定关键词，纯通用闲聊模式匹配
- **查询分类**：移除"镇馆之宝""国宝""材质""工艺"等领域特定词
- **上下文格式**：从 `【文物：xxx】` 改为通用 `【xxx】`

#### 新增文件
- `src/project.py` - 项目管理模块
- `data/projects/museum.json` - 博物馆项目配置
- `data/projects/enterprise.json` - 企业项目配置
- `scripts/generate_mock_project_data.py` - 多项目 Mock 数据生成器

#### 修改文件
- `src/rag_pipeline.py` - 集成 project_id，按项目选择 Prompt/集合/BM25/路径
- `src/vector_store.py` - 支持 project_id，自动使用项目专属集合名和存储路径
- `scripts/build_knowledge_base.py` - 新增 --project 和 --list-projects 参数
- `app.py` - 新增项目下拉选择器

---

### v1.2.1 (2024-01)

#### 模型迁移
- **重排序模型迁移**：从已下线的 `gte-rerank` 迁移至 `qwen3-reranker-4b`（默认），支持 `qwen3-reranker-8b`（更准）
- 配置项 `RERANKER_MODEL` 默认值已更新，`.env.example` 同步更新

#### 文档更新
- 技术栈表格更新：重排序环节改为 Qwen3-Reranker
- 更新日志新增 v1.2.1 条目

---

### v1.2.0 (2024-01)

#### 新增功能
- **闲聊路由**：自动识别闲聊问题（你好、天气、你是谁），直接 LLM 回答，不走 RAG
- **检索结果可视化**：Web UI 右侧面板实时显示检索到的文物、相关度得分、切片类型
- **响应时间显示**：每条回答底部显示总耗时（分类、检索、重排序、LLM 各阶段）
- **智能切片 v2**：从 4 切片优化为 3 切片（summary/detail/significance），信息密度更高
- **查询分类 v2**：基于评分机制，准确识别 15+ 种查询模式
- **多轮对话**：支持追问（"它有多重？"），保留最近 4 轮对话历史
- **增量更新**：`add_artifacts()` 方法支持增量添加文物，无需全量重建
- **回答质量评估**：`verify_answer_grounding()` 检查 LLM 回答是否基于检索上下文

#### 性能优化
- **闲聊路由**：非知识库问题跳过检索，响应时间 < 500ms
- **重排序优化**：检索结果 <= 3 条时跳过重排序，节省 200ms
- **响应时间分解**：每阶段耗时可追踪
- **高频问题 Embedding 预计算**：16 个高频问题模式离线预计算，命中率 ~30-50%
- **Qdrant 全内存模式**：可选，所有向量加载到 RAM，检索耗时 < 5ms

#### 响应时间分析

| 场景 | 总耗时 | 检索 | LLM 首字 | 说明 |
|------|--------|------|---------|------|
| 闲聊问候 | **~300-500ms** | 0ms | 300-500ms | 直接 LLM，不走 RAG |
| 知识库（缓存命中） | **~500-800ms** | < 5ms | 500-800ms | Embedding 预计算命中 |
| 知识库（流式） | **~1-2s 首字** | 300-500ms | 500-800ms | 并行检索 + 流式 LLM |
| 知识库（非流式） | **~2-4s 完整** | 300-500ms | 1500-3000ms | 等待 LLM 完整输出 |

#### 关于本地 sentence-transformers

**是什么**：将开源 Embedding 模型（如 `BAAI/bge-large-zh-v1.5`）直接部署在 GPU 服务器本地，替代百炼 Embedding API。

**vs 在线 API**：

| 对比 | 百炼 Embedding API | 本地 sentence-transformers |
|------|-------------------|--------------------------|
| 延迟 | ~200-300ms（网络传输） | **~10-30ms**（本地推理） |
| 费用 | 按量计费 | **免费** |
| GPU 需求 | 不需要 | 需要 ~2GB 显存 |
| 精度 | 商业级（text-embedding-v3） | 开源模型稍弱（～5% 差距） |
| 维护 | 零维护 | 需管理模型文件 |

**当前选择**：暂不启用本地模型，因为：
1. Embedding 缓存已覆盖大部分高频场景
2. 百炼 API 精度更高且零维护
3. GPU 显存可留给 PaddleOCR 等其他任务

如需启用，在 `embed_query()` 中添加 sentence-transformers 分支即可，代码已预留接口。

#### 配置 Qdrant 全内存模式

```bash
# 在 .env 中设置
QDRANT_MEMORY_MODE=true

# 然后重新构建知识库
python scripts/build_knowledge_base.py --source mixed
```

注意：全内存模式下数据在重启后需要重新构建。建议在服务器上做一次构建后保持运行，配合 `enable_cache=true` 使用。

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
│  │ Qdrant    │    │ Qdrant   │    │ Qdrant   │  ← 独立集合      │
│  │ museum    │    │ enterpr. │    │ custom   │                   │
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

### 代码泛化性

- **Prompt 模板**：从"中国文物专家"改为"知识助手"，不绑定任何领域
- **`is_kb_related()`**：移除领域特定关键词，纯通用闲聊模式匹配
- **查询分类**：移除"镇馆之宝""国宝""材质""工艺"等领域特定词
- **上下文格式**：从 `【文物：xxx】` 改为通用 `【xxx】`

## 项目概述

本项目实现了一个完整的文物知识库 RAG 系统，用于回答关于中国文物的各种问题，特别是**推荐类问题**（如"推荐一些代表性的文物"）。

### 核心能力

| 能力 | 描述 | 示例 |
|------|------|------|
| **文物推荐** | 基于知识库推荐代表性文物，覆盖不同朝代、类别 | "推荐一些代表性的文物" |
| **事实查询** | 精确回答文物的属性信息 | "司母戊鼎有多重？" |
| **比较分析** | 对比不同文物或类别的异同 | "青铜器和瓷器有什么区别？" |
| **开放讨论** | 综合性、开放性的文物话题讨论 | "谈谈唐代的工艺美术成就" |

### 设计特点

- **多尺度切片**：每件文物生成多个不同粒度的文本切片（短文本、描述、意义、全文），提升检索召回率
- **混合检索**：语义检索（向量） + 关键词检索（BM25）双通道，RRF 算法融合排序
- **多样性重排序**：MMR（Maximal Marginal Relevance）算法确保推荐结果的多样性
- **查询类型自适应**：自动识别用户意图，选择最合适的 Prompt 模板
- **纯 API 架构**：依赖阿里云百炼在线 API，无需本地 GPU 资源


### v1.1.0 (2024-01)
#### 新增功能
- **Web UI 问答界面**：基于 Gradio 的浏览器端问答页面，支持流式输出
- **多格式文档支持**：PDF、Word、TXT、Markdown、图片(OCR) 等多种格式自动解析
- **LRU 缓存系统**：Embedding、LLM 响应、检索结果三层缓存，相同问题秒回
- **Mock 数据生成器**：生成 50+ 件覆盖各朝代、各类别的文物测试数据
- **Conda 环境配置**：`environment.yml` 一键创建隔离环境，不影响其他项目
- **GPU 一键部署脚本**：`setup_gpu.sh` 自动完成部署全流程

#### 性能优化
- **并行检索**：语义检索和 BM25 检索同时执行，检索速度提升 ~40%
- **流式输出**：首 token 延迟优化，检索完成后立即返回
- **上下文裁剪**：自动裁剪上下文至 10000 字符内，防止 Token 超限
- **预热机制**：启动时预先加载知识库，减少首次查询延迟

#### 模型切换
- 默认 LLM 从 `qwen-max` 切换为 `qwen-plus`（速度快 2 倍，成本低 10 倍）

#### Bug 修复
- 修复 `embeddings.py` 中 `embed_batch` None 过滤导致长度不匹配问题
- 修复 `llm.py` 中 `stream=True` 参数导致运行时崩溃问题
- 修复 `rag_pipeline.py` 中 `query_stream` 方法逻辑错误
- 修复 `vector_store.py` 中 hash 冲突和 metadata 过滤失效问题
- 修复 `chunking.py` 中空列表除零错误
- 修复 `document_loader.py` 中 f-string 嵌套引号语法错误（Python 3.10 兼容）
- 修复多处未使用 import 和参数未传递问题

### v1.0.0 (2024-01)
- 初始版本
- 15 件代表性文物数据
- 完整 RAG 流水线：多尺度切片 → 混合检索 → 重排序 → LLM 生成
- 支持推荐、事实、比较、开放四类问题
- 纯 API 架构，无需本地 GPU

---
---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户接口层                                    │
│  ┌─────────────┐  ┌────────────────┐  ┌─────────────────────────┐  │
│  │ 交互式 CLI   │  │ 单次查询模式    │  │ Python API (SDK)       │  │
│  └──────┬──────┘  └───────┬────────┘  └───────────┬─────────────┘  │
└─────────┼──────────────────┼──────────────────────┼────────────────┘
          │                  │                      │
┌─────────▼──────────────────▼──────────────────────▼────────────────┐
│                      RAG 流水线（rag_pipeline.py）                  │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────────┐  │
│  │ 查询分类  │→ │ 混合检索  │→ │ 重排序   │→ │ Prompt构建 + LLM  │  │
│  │ classifier│  │ hybrid   │  │ reranker │  │ generation         │  │
│  └──────────┘  └────┬─────┘  └──────────┘  └────────────────────┘  │
└─────────────────────┼──────────────────────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────────────────────┐
│                     知识库层                                        │
│                                                                     │
│  ┌─────────────┐  ┌──────────────────┐  ┌──────────────────────┐   │
│  │ 向量数据库   │  │ BM25 倒排索引    │  │ 切片缓存 (JSON)      │   │
│  │ (Qdrant)    │  │ (rank_bm25)      │  │ (本地持久化)         │   │
│  └─────────────┘  └──────────────────┘  └──────────────────────┘   │
└─────────────────────┬──────────────────────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────────────────────┐
│                     数据处理层                                      │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐   │
│  │ 数据加载      │→ │ 多尺度切片    │→ │ Embedding (百炼 API)   │   │
│  │ data_loader   │  │ chunking     │  │ text-embedding-v3     │   │
│  └──────────────┘  └──────────────┘  └────────────────────────┘   │
└─────────────────────┬──────────────────────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────────────────────┐
│                     原始数据层                                      │
│              data/raw/artifacts.json (15件代表性文物)               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 技术栈（完整）

### RAG 全流程技术栈一览

| 环节 | 技术选型 | 版本 | 功能 | 部署方式 |
|------|---------|------|------|---------|
| **数据加载** | 自定义 `DataLoader` | v1 | 加载 JSON/CSV 文物数据，字段映射标准化 | 内置，零依赖 |
| **多格式文档解析** | pypdf / python-docx / python-pptx / PaddleOCR | 最新 | 解析 PDF、Word、PPT、图片(OCR) 等格式，统一转为 Artifact 对象 | 本地（GPU 可选） |
| **文档切片** | 自定义 `SmartChunking` | v2 | 每件文物生成 3 个切片（summary/detail/significance），信息密度高、重叠少 | 内置 |
| **Embedding 生成** | 阿里云百炼 `text-embedding-v3` | v3 | 1024 维向量，中文语义理解优秀，批处理并发 | **在线 API** |
| **Embedding 缓存** | 自定义 `EmbeddingCache` | v2 | 高频问题预计算 + 精确匹配 + 模式匹配，持久化到磁盘，命中率 ~30-50% | 内置 |
| **向量数据库** | **Qdrant**（本地模式） | ≥1.9 | 本地持久化，零配置；支持全内存模式（RAM 检索 < 5ms） | 本地 |
| **关键词检索** | **rank-bm25**（BM25Okapi） | ≥0.2 | 中文 unigram+bigram 分词，与语义检索互补 | 内置（内存索引） |
| **混合检索融合** | 自定义 `HybridRetriever` | v2 | 语义检索 + BM25 并行执行，RRF 算法融合排序，按文物去重 | 内置 |
| **重排序** | 百炼 **`qwen3-reranker-4b`**（默认）/ `qwen3-reranker-8b` / 本地 TF-IDF（降级） | - | 对检索结果精排，gte-rerank 已下线，迁移至 Qwen3-Reranker 系列 | 在线 API / 本地 |
| **LLM 问答** | 阿里云百炼 **`qwen-plus`**（默认）/ `qwen-max` | 3.7+ | 日常问答用 qwen-plus（快 2 倍，便宜 10 倍），复杂推理用 qwen-max | **在线 API** |
| **LLM 缓存** | 自定义 `LRUCache` | v1 | 相同问题不重复调用 API，TTL 30 分钟自动过期 | 内置 |
| **查询分类** | 自定义 `classify_query` | v2 | 基于评分机制，15+ 种模式，准确识别推荐/事实/比较/开放/闲聊五类 | 内置 |
| **闲聊路由** | 自定义 `is_kb_related` | v1 | 自动识别问候、天气等非知识库问题，直接 LLM 回答，跳过 RAG | 内置 |
| **上下文裁剪** | 自定义 `_trim_context` | v1 | 按相关性保留完整段落，上限 10000 字符，防止 Token 超限 | 内置 |
| **多轮对话** | 对话历史传递 | v1 | 保留最近 4 轮对话（8 条消息），支持追问"它有多重？" | 内置 |
| **回答质量评估** | 自定义 `verify_answer_grounding` | v1 | 检查 LLM 回答中的文物名称是否在检索上下文中，防幻觉 | 内置 |
| **增量更新** | 自定义 `add_artifacts` | v1 | 增量添加新文物，无需全量重建知识库 | 内置 |
| **Web UI** | **Gradio** | ≥4.44 | 浏览器端问答界面，流式输出，检索结果可视化，响应时间显示 | 本地 |
| **CLI 交互** | **Rich**（rich.console） | ≥13.7 | 终端交互式问答，支持彩色输出、表格、Markdown 渲染 | 内置 |
| **缓存层** | 自定义三层缓存 | v2 | Embedding 缓存（持久化）+ LLM 响应缓存（LRU）+ 检索结果缓存（LRU） | 内置 |
| **配置管理** | **Pydantic Settings** | ≥2.1 | 支持 .env 文件 + 环境变量 + 类型校验 + IDE 自动补全 | 内置 |
| **日志** | **Loguru** | ≥0.7 | 彩色控制台输出 + 文件日志（自动轮转、保留 30 天） | 内置 |
| **包管理** | **Conda**（environment.yml） | - | 隔离环境，不影响其他项目，一键创建 | 系统 |
| **GPU 部署** | 自定义 `setup_gpu.sh` | v1 | 自动检测 NVIDIA 驱动、创建 Conda 环境、安装依赖、构建知识库 | 系统 |

### 数据流图

```
用户输入
   │
   ▼
┌─────────────────────────────────────────────────────────────────┐
│ 1. 路由判断 (is_kb_related)                                   │
│    ├── 闲聊/非知识库 → 直接调用 LLM (qwen-plus) ← 无检索, 最快 │
│    └── 知识库相关 → 进入 RAG 流水线                            │
│                          │                                      │
│                          ▼                                      │
│ 2. 查询分类 (classify_query) 识别推荐/事实/比较/开放            │
│                          │                                      │
│                          ▼                                      │
│ 3. 混合检索 (并行执行) ─┬─ 语义检索: 百炼 Embedding → Qdrant   │
│                          └─ BM25检索: rank-bm25 (内存索引)      │
│                          │  RRF 融合 + 按文物去重                │
│                          ▼                                      │
│ 4. 重排序 (gte-rerank API / 本地 TF-IDF)                       │
│                          │                                      │
│                          ▼                                      │
│ 5. 构建上下文 (裁剪 ≤ 10000 字符) + 选择 Prompt 模板           │
│                          │                                      │
│                          ▼                                      │
│ 6. LLM 生成 (qwen-plus 流式/非流式)                             │
│                          │                                      │
│                          ▼                                      │
│ 7. 输出 (Web UI / CLI) + 检索结果可视化                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 1️⃣ 环境准备

```bash
# 克隆项目
cd E:/project/agent_project/pi/test

# 创建虚拟环境（推荐）
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
# source venv/bin/activate

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
| `/clear` | 清屏 |
| `/exit` 或 `/quit` | 退出系统 |

### 示例查询

```bash
# 推荐类问题
python scripts/run_qa.py -q "推荐一些代表性的文物"
python scripts/run_qa.py -q "给我推荐几个镇馆之宝"
python scripts/run_qa.py -q "有哪些著名的国宝文物"

# 事实类问题
python scripts/run_qa.py -q "司母戊鼎有多重"
python scripts/run_qa.py -q "清明上河图在哪里展出"
python scripts/run_qa.py -q "越王勾践剑的材质是什么"

# 比较类问题
python scripts/run_qa.py -q "青铜器和瓷器有什么区别"
python scripts/run_qa.py -q "比较一下司母戊鼎和毛公鼎"

# 开放类问题
python scripts/run_qa.py -q "谈谈唐代的工艺美术成就"
```

---

## 数据处理流程

### 1. 原始数据

数据存储在 `data/raw/artifacts.json`，每件文物包含以下字段：

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
  "tags": ["国宝", "青铜器", "商代", "礼器", "镇馆之宝"],
  "importance": 5
}
```

### 2. 多尺度切片

每件文物生成 **4 种类型**的切片：

| 切片类型 | 内容 | 目的 | 示例 |
|---------|------|------|------|
| **short** | 名称 + 朝代 + 类别 | 快速关键词匹配 | "司母戊鼎（后母戊鼎），商代晚期，青铜器" |
| **description** | 详细描述信息 | 事实类问题匹配 | 包含尺寸、重量、出土信息等 |
| **significance** | 历史意义 + 文化价值 | 推荐类问题匹配 | 包含"代表性""巅峰""价值"等关键词 |
| **full** | 完整文本 | 综合检索兜底 | 所有字段拼接 |

### 3. Embedding 生成

使用百炼 `text-embedding-v3` 模型，每批 16 条并发，自动重试。

### 4. 数据入库

- **向量数据库**：Qdrant 本地持久化（`data/processed/qdrant_db/`）
- **BM25 索引**：内存中构建，用于关键词检索
- **切片缓存**：JSON 格式保存（`data/processed/chunks.json`）

---

## RAG 问答流程

### 推荐类问题完整处理流程

以用户提问 **"推荐一些代表性的文物"** 为例：

```
Step 1: 查询分类
────────────────────────────────────────────────────────────
输入: "推荐一些代表性的文物"
输出: QueryType.RECOMMENDATION
逻辑: 命中关键词 ["推荐", "代表性"]

Step 2: 问题改写（隐式）
────────────────────────────────────────────────────────────
生成多个子查询维度:
  - "代表性文物"
  - "镇馆之宝 国宝"
  - "著名文物 推荐"
  - "重要文物 价值"

Step 3: 混合检索
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
   同一件文物只保留最高分结果

输出: Top 10 文物（覆盖不同朝代、类别）

Step 4: 重排序（可选）
────────────────────────────────────────────────────────────
使用百炼 gte-rerank API 对 Top 10 进行精细排序
或本地 TF-IDF 余弦相似度自动降级

输出: Top 5 文物（精排后）

Step 5: 构建 Prompt
────────────────────────────────────────────────────────────
系统提示词模板（推荐类）:
  你是一位专业的中国文物专家...
  推荐原则：
  1. 从参考信息中挑选 3~5 件最具代表性的文物
  2. 每件文物需包含：名称、朝代、介绍、推荐理由
  3. 尽量覆盖不同朝代、不同类别
  4. 推荐理由要具体
  5. 如果参考信息不足，如实说明

  参考信息：
  【文物：司母戊鼎】...
  【文物：清明上河图】...
  ...

  用户问题：推荐一些代表性的文物

Step 6: LLM 生成
────────────────────────────────────────────────────────────
调用 Qwen-Max → 生成结构化推荐回答

Step 7: 输出
────────────────────────────────────────────────────────────
### 推荐清单

**1. 司母戊鼎（商代晚期）**
- **简介**：目前已知中国最重的青铜器，重832.84公斤
- **推荐理由**：代表商代青铜铸造巅峰，中华文明象征
- **收藏地点**：中国国家博物馆

**2. 清明上河图（北宋）**
...
```

---

## 推荐类问题处理详解

### 为什么推荐类问题需要特殊处理？

推荐类问题不同于事实问答，它需要：

1. **多样性**：推荐结果应覆盖不同朝代、不同类别
2. **代表性判断**：需要理解"代表性"的内涵（技术成就、艺术价值、历史意义等）
3. **结构化输出**：结果需要清晰、有条理，每个推荐项都要有理由

### 关键优化策略

#### 1. 多尺度切片设计

```
推荐类问题独有的切片：significance 类型
┌──────────────────────────────────────────────┐
│ 文物名称：司母戊鼎                            │
│ 朝代：商代晚期                                │
│ 历史意义：代表了商代青铜铸造技术的巅峰...      │
│ 文化价值：作为中国国家博物馆的镇馆之宝...      │
└──────────────────────────────────────────────┘
```
这个切片专门用于匹配"代表性""价值""意义"等推荐类关键词。

#### 2. 去重策略

```python
# 混合检索后去重：同一件文物只保留最高分
seen_artifacts = set()
for chunk_id, score in ranked:
    if chunk.artifact_id not in seen_artifacts:
        results.append((chunk, score))
        seen_artifacts.add(chunk.artifact_id)
```

#### 3. 查询分类逻辑

```python
recommend_keywords = [
    "推荐", "介绍", "有哪些", "什么", "代表性",
    "著名", "值得", "必看", "镇馆之宝", "国宝",
    "给我推荐", "推荐一些", "推荐几个",
]
```

#### 4. Prompt 模板优化

推荐类使用专门的 `SYSTEM_PROMPT_RECOMMEND` 模板：
- 明确要求 3~5 件
- 要求覆盖不同朝代、类别
- 要求每件都给出推荐理由
- 禁止编造

---

## API 参考

### RAGPipeline 类

```python
from src.rag_pipeline import RAGPipeline

# 初始化
pipeline = RAGPipeline(
    embedding_model="text-embedding-v3",  # Embedding 模型
    llm_model="qwen-max",                  # LLM 模型
    vector_size=1024,                      # 向量维度
    local_mode=True,                       # 本地 Qdrant 模式
)

# 构建知识库
stats = pipeline.build_knowledge_base(
    data_path="data/raw/artifacts.json",  # 数据文件
    overwrite=True,                        # 覆盖已有知识库
)

# 执行查询
result = pipeline.query(
    question="推荐一些代表性的文物",  # 用户问题
    top_k=10,                          # 检索 Top-K
    rerank=True,                       # 是否重排序
)

# 结果字段
# result["answer"]        - LLM 生成的回答
# result["query_type"]    - 查询类型（recommendation/factual/...）
# result["retrieved_chunks"] - 检索到的上下文
# result["context"]       - 拼接后的上下文文本
```

### 其他核心模块

```python
# 数据加载
from src.data_loader import DataLoader
artifacts = DataLoader.load("data/raw/artifacts.json")

# 切片
from src.chunking import ChunkingPipeline, MultiScaleChunking
pipeline = ChunkingPipeline(strategy=MultiScaleChunking())
chunks = pipeline.process(artifacts)

# 混合检索
from src.retriever import HybridRetriever
results = retriever.retrieve(query="青铜器", top_k=10)
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

### 文档加载流程

```
文档文件 (PDF/Word/TXT/图片...)
    │
    ▼
┌─────────────────────────────────────┐
│ 自动检测文件格式                      │
│ 选择对应解析器                        │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 解析文档内容                          │
│ PDF → pypdf 提取文本                  │
│ Word → python-docx 提取段落+表格      │
│ 图片 → PaddleOCR GPU 加速识别         │
│ TXT  → 直接读取                       │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 转换为统一的 Artifact 对象            │
│ 包含: 标题、内容、元数据、来源信息     │
└─────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────┐
│ 进入标准 RAG 流水线                   │
│ 切片 → Embedding → 向量数据库入库     │
└─────────────────────────────────────┘
```

### 使用示例

```bash
# 1. 先生成测试文档
python scripts/generate_test_docs.py

# 2. 从文档目录构建知识库
python scripts/build_knowledge_base.py --source docs --doc-path ./data/raw/docs

# 3. 混合模式（JSON文物数据 + 文档）
python scripts/build_knowledge_base.py --source mixed --doc-path ./data/raw/docs

# 4. 禁用 OCR（如果不需要图片识别）
python scripts/build_knowledge_base.py --source docs --doc-path ./data/raw/docs --no-ocr

# 5. 使用 Tesseract OCR 替代 PaddleOCR
python scripts/build_knowledge_base.py --source docs --doc-path ./data/raw/docs --ocr-engine tesseract
```

### 在代码中使用

```python
from src.document_loader import DocumentLoader

# 初始化加载器
loader = DocumentLoader(enable_ocr=True, ocr_engine="paddle")

# 加载单个文件
doc = loader.load_file("data/raw/docs/txt/青铜器概述.txt")
print(f"标题: {doc.title}, 内容长度: {len(doc.content)}")

# 加载整个目录
docs = loader.load_directory("data/raw/docs", recursive=True)
print(f"共加载 {len(docs)} 个文档")

# 转换为 Artifact 对象（用于 RAG 流水线）
artifacts = loader.load_all_as_artifacts("data/raw/docs")
```

### OCR 引擎选择

| 引擎 | 优势 | 安装方式 |
|------|------|---------|
| **PaddleOCR**（推荐） | GPU 加速，中文识别率最高 | `pip install paddlepaddle-gpu paddleocr` |
| **Tesseract OCR**（备选） | 开源免费，多语言支持 | `pip install pytesseract` + 系统安装 tesseract-ocr |

在 GPU 服务器上，PaddleOCR 可以充分利用 RTX 3090 的算力，大幅提升批量图片的识别速度。

---

## Conda 环境部署（GPU 服务器）

项目提供了完整的 Conda 环境配置，确保在 GPU 服务器上快速部署且不影响其他项目。

### 一键部署脚本

```bash
# 进入项目目录
cd /path/to/cultural-relics-rag

# 运行部署脚本（自动完成所有步骤）
bash setup_gpu.sh
```

### 手动部署步骤

#### 1. 创建 Conda 环境

```bash
# 从 environment.yml 创建环境
conda env create -f environment.yml

# 激活环境
conda activate cultural-relics-rag
```

#### 2. 安装 PaddleOCR GPU 支持

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

#### 4. 生成测试数据

```bash
# 生成 50 件 Mock 文物数据
python scripts/generate_mock_data.py -n 50

# 生成多格式测试文档
python scripts/generate_test_docs.py
```

#### 5. 构建知识库

```bash
# 混合模式（JSON + 文档）
python scripts/build_knowledge_base.py --source mixed

# 或仅从文档构建
python scripts/build_knowledge_base.py --source docs --doc-path ./data/raw/docs
```

#### 6. 运行问答

```bash
# 交互式
python scripts/run_qa.py

# 单次查询
python scripts/run_qa.py -q "推荐一些代表性的文物"
```

### 环境隔离说明

- Conda 环境名称: `cultural-relics-rag`
- 所有依赖均安装在该环境内，不影响系统 Python 或其他项目
- 使用 `conda activate cultural-relics-rag` 激活
- 使用 `conda deactivate` 退出

```bash
# 查看所有 Conda 环境
conda env list

# 删除环境（如需重建）
conda env remove -n cultural-relics-rag
```

---

## 部署指南

### 开发环境（Windows）

```bash
# 本地运行
python scripts/build_knowledge_base.py
python scripts/run_qa.py
```

### 生产环境（GPU 服务器 Linux）

本项目设计为**纯 API 调用**，GPU 服务器主要用于：
1. **Qdrant 服务**：建议部署独立的 Qdrant 服务端
2. **高并发场景**：多路 API 调用

```bash
# 1. 安装系统依赖
sudo apt-get update
sudo apt-get install -y python3 python3-pip

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 配置环境变量
export DASHSCOPE_API_KEY="your-api-key"

# 4. 可选：部署 Qdrant 服务
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant

# 5. 修改配置（.env 或环境变量）
QDRANT_HOST=localhost
QDRANT_PORT=6333
LOCAL_MODE=false

# 6. 构建知识库
python scripts/build_knowledge_base.py

# 7. 启动 API 服务（可扩展为 Web API）
python scripts/run_qa.py --question "推荐一些代表性的文物"
```

### 使用第三方 API 注意事项

本项目完全依赖阿里云百炼 API，无需本地 GPU：
- **Embedding**：text-embedding-v3（百炼 API）
- **LLM**：qwen-max（百炼 API）
- **Rerank**：gte-rerank（百炼 API）

如果百炼 Rerank API 不可用，会自动降级到本地 TF-IDF 重排序。

---

## 性能优化

### 已实施的优化（不影响准确率）

| 优化项 | 收益 | 对准确率影响 | 原理 |
|--------|------|------------|------|
| **并行检索** | 检索速度提升 ~40% | 无影响 | 语义检索和 BM25 检索同时执行 |
| **HTTP Session 复用** | 首 token 延迟减少 ~200ms | 无影响 | 复用 TCP 连接，避免三次握手 |
| **LRU 响应缓存** | 重复问题秒回 | 无影响 | 相同问题缓存 30 分钟，有 TTL 过期 |
| **上下文裁剪** | 减少 Token 消耗 10-30% | 无影响 | 10000 字符上限，按相关性保留完整段落 |

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
5. **预热机制**：`warmup()` 在启动时预先建立 HTTP 连接

---

## Web UI 问答界面

系统提供了基于 Gradio 的 Web 界面，支持流式输出和实时展示检索结果。

### 启动方式

```bash
# 确保已构建知识库
python scripts/build_knowledge_base.py --source mixed

# 启动 Web UI
python app.py

# 启动后访问 http://127.0.0.1:7860
```

### 参数说明

```bash
python app.py --help

# 指定端口
python app.py --port 8080

# 允许外部访问
python app.py --host 0.0.0.0 --port 7860

# 创建公开链接（分享给他人测试）
python app.py --share

# 禁用流式输出
python app.py --no-stream
```

### 页面功能

- **流式输出**：逐字显示答案，首字更快
- **示例问题**：一键点击尝试文物问题、闲聊等
- **检索结果可视化**：右侧面板实时显示检索到的文物名称、相关度得分、切片类型
- **闲聊路由**：自动识别问候、天气等非知识库问题，直接 AI 回答，跳过检索
- **响应时间**：每条回答底部显示总耗时（仅非流式）
- **状态监控**：实时显示知识库统计和模型配置
- **对话历史**：支持多轮追问，保存最近 4 轮对话

---

## 项目结构

```
E:/project/agent_project/pi/test/
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
│   │   │   └── data.json            #   17 条文物/展览/参观须知
│   │   ├── enterprise/              # 企业项目原始数据
│   │   │   └── data.json            #   14 条企业概况/产品/案例/文档
│   │   ├── artifacts.json           # 旧版单项目数据
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
│   ├── reranker.py                  # 重排序模块（Qwen3-Reranker）
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
│   ├── generate_mock_data.py        # 单项目 Mock 数据生成器（旧）
│   └── generate_test_docs.py        # 多格式测试文档生成器
│
└── tests/                           # 单元测试
    ├── __init__.py
    └── test_pipeline.py             # 流水线测试
```

---

## 常见问题

### Q: API Key 在哪里获取？
A: 登录 [阿里云百炼平台](https://bailian.console.aliyun.com/) → 右上角"API 密钥" → 创建 API Key。

### Q: 费用如何？
A: 百炼 API 按量计费，qwen-max 约 0.04元/千 tokens，text-embedding-v3 约 0.0007元/千 tokens。日常使用费用很低。

### Q: 如何添加更多文物数据？
A: 编辑 `data/raw/artifacts.json`，按照已有格式添加新文物，然后重新运行 `build_knowledge_base.py`。

### Q: 推荐结果不够多样化怎么办？
A: 可以调整 `retriever.py` 中的 MMR 参数，或在 `rag_pipeline.py` 的 Prompt 中强调多样性要求。

### Q: 如何切换成其他 LLM 模型？
A: 修改 `src/config.py` 中的 `llm_model_name`，或设置环境变量 `LLM_MODEL_NAME`。百炼支持 qwen-max、qwen-plus、qwen-turbo 等。

### Q: 为什么回答会出现编造的内容？
A: 如果检索到的上下文不足，LLM 可能会"幻觉"。建议：
1. 增加知识库中的文物数量
2. 降低 `llm_temperature` 参数
3. 在 Prompt 中强调"如果参考信息不足，请如实说明"

---

## 许可证

本项目仅供学习和研究使用。

