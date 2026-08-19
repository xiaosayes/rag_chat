# 新会话任务书：数字人智能问答效果优化（2026-08-18 生成）

> 粘贴本文件全文作为新窗口的开场 prompt。

## 0. 本次任务焦点

**优化数字人一体机的智能问答效果**（答案质量、相关性、人设一致性、播报友好度、响应速度感知）。
前端/语音链路已验收完成，本轮专注「问答内容本身」。开工前先与我确认本轮具体优化目标清单，
再逐项 TDD 实施。

## 1. 项目与分支现状

- 项目：`E:/project/agent_project/pi/test`（文化知识库 RAG 问答系统）。
- **数字人前端轮已全部完成并由我手工合并进 main**：HEAD=`1d439e2 前端功能开发`
  （其下依次为 `be5e2be` web-042、`17d3f06` web-041、`7c575ca` web-040…共 20+ 个 `feat(web):` 提交）。
- 测试基线（main 上实测）：`python -m pytest tests/ -q` = **709 passed**；
  `cd frontend && npx vitest run` = **55 passed**。任何改动后必须保持全绿。
- 分支约定：新工作请从 main 切新特性分支（如 `feature/qa-polish`），**不直接提交 main**，
  我验收后手工合并。先 `git status` 确认工作区干净。

## 2. 硬约束（红线，逐条遵守）

1. **冻结区零改动**：`src/` 全部内核、`app.py`（Gradio 控制台）、`.env`——除非我逐项明确批准。
   薄层 `kiosk_server/`（全部新文件）与 `frontend/` 是可动区。
2. `data/front_ui/`（参考前端与设计稿）**只读**；`docs/`、`data/` 已 gitignore。
3. 密钥（XFYUN/DashScope）只在服务端，永不进前端、永不打印。
4. TDD：先写失败测试再实现；外部 API 一律 mock；真实 API 仅用于冒烟脚本验证。
5. 记账约定：提交前缀 `feat(web):`/`fix(web):`；注释与测试标签 `web-xxx`（已用到 **web-042**，
  本轮从 **web-043** 起）；每轮结束更新 README 变更日志 + `code_review_report_v3.md` 累加
  （当前到 §12 + web-039/040/041/042 增补）。
6. Gradio 6.22 依赖钉：`starlette<1.4` + `fastapi<1.0`。

## 3. 问答链路全景（优化前必读）

用户问题 → 答案有三条路径（前两条在冻结内核，第三条是薄层）：

| 路径 | 触发 | 说明 |
|---|---|---|
| A. 知识库 RAG | 意图=KB 相关 + 检索相关度达标 | `RAGPipeline.query_stream`：意图分类→混合检索→重排→LLM 流式 |
| B. 内核闲聊/联网 | 意图=闲聊，或检索空/相关度低且 `_should_enable_search`=True（开放/未知/时效类） | 内核 chitchat 提示词 + `enable_search` |
| C. 薄层联网兜底 | 检索相关度低但**事实类**（内核策略不联网，原固定拒答） | `kiosk_server/web_fallback.py` 识别拒答模板→百炼 enable_search 流式（web-036） |

播报/传输层（薄层 `chat.py`）：句边界喂 TTS、首播硬地板 12 字（web-030）、看门狗积压判据
（web-040 修复了慢流播报中途死亡）、进程级 max_tokens=320（web-041）。

## 4. 已知问答效果痛点（全部实测在案）

1. **人设漂移**：内核 chitchat 提示词是「小虎/家博会」——联网/闲聊路径答案会自称小虎、
   提家博会（一体机是「湘小图」图书馆场景）。薄层兜底已用湘小图提示词+裁历史缓解（web-036/040），
   但**路径 B 的人设属内核**（改动需我批准）。
2. **Markdown 残留**：内核路径答案含 `**` 粗体标记直接上屏（薄层兜底已剥离，路径 A/B 未处理）。
3. **意图分类随机性**：同一问题「有什么电影推荐？」两次分别走路径 B 与路径 C（LLM 分类不稳定）。
4. **重排冷缓存 ~40s**（DashScope rerank API 首调慢，二次走缓存）；搜索生成流速不可控。
5. **知识库数据不匹配**：当前 KB 是家博会测试数据（174 chunks），而 persona/预设题全是
   图书馆风格——KB 类问题答非所问（说展会时间）。换真知识库属数据运营，不是代码问题。
6. **口语化/播报友好度**：答案带列表符号、英文术语时 TTS 朗读生硬；`clean_text_for_tts`
   只清洗不进 LLM 提示词约束。
7. 预设问题池：`data/kiosk/preset_questions.json`（16 条图书馆风格，可自行扩充）。

## 5. 可调杠杆清单（按权限分级）

**薄层/前端（可直接动，TDD 覆盖）**：
- `kiosk_server/web_fallback.py`：`FALLBACK_SYSTEM_PROMPT`、`FALLBACK_MAX_TOKENS`、历史条数；
- `kiosk_server/services.py`：`KIOSK_ANSWER_MAX_TOKENS`（进程级钳制）；
- `kiosk_server/chat.py`：喂入切分/看门狗参数；
- 前端展示层文本清洗（如剥离 `**`、规整空白）；
- 预设池 JSON、纠词典 `data/voice/asr_dict.json`、主题动作表 `THEME_RULES`。

**冻结内核（须我逐项批准后才可动）**：
- chitchat/RAG 提示词模板（人设统一为湘小图）；
- `_should_enable_search` 触发策略（事实类也联网？）；
- rerank 超时/缓存策略、意图分类稳定性（温度/few-shot）。

## 6. 评测与验证工具（既有，直接用）

- `scripts/qa_harness.py`、`run_qa.py`、`qa_multiturn.py`：问答批量回归/多轮评测（先读源码了解用法）；
- `scripts/smoke_kiosk_ws.py --port 7861 "<问题>"`：薄层全链冒烟（首文本/首音频/全文/音频时长）；
- 计时分解参考：意图+检索+重排+LLM 首字 1.2~2.3s（KB 内）；首播 2.06~2.91s（web-030 后）。
- **建议开工第一步**：用 qa_harness 建一组「图书馆场景 20 题基线集」（KB 内/闲聊/时效/事实外
  各 5 题），先测出现状基线，再谈优化——一切用数据说话。

## 7. 运行方式

```bash
python -m kiosk_server --host 127.0.0.1 --port 7861   # 薄层（与 Gradio:7860 因 Qdrant 文件锁互斥）
cd frontend && npm run dev                             # 前端 :5173
```

## 8. 开工暗号

请回复确认：已读完 `code_review_report_v3.md` §11/§12、README v1.6.0-pre 变更日志、
`kiosk_server/web_fallback.py` 与 `src/rag_pipeline.py` 的三条回答路径代码；
然后与我逐项确认本轮优化目标清单（不要擅自扩大范围）。
