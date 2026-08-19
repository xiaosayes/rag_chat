## 0. 本次任务焦点

**一体机公网部署（内网 → 公网）**。部署计划已在上一窗口定稿（见 §4，含全部实证事实）。
开工前先与我确认三个决策点（A 公网出口方式 / B 域名证书 / C 鉴权模式），
再按「与出口方式无关的部分先行（服务端 token + 前端接线），选定方案后细化反代」的顺序逐项 TDD 实施。

## 1. 项目与分支现状

- 项目：`E:/project/agent_project/pi/test`（文化知识库 RAG 问答系统）。
- **分支**：main 曾= `1d439e2`；上一窗口 `feature/qa` 已完成并推送 origin
  （Gitea `http://localhost:3000/gitea_admin/rag_chat_project.git`），共 8 个提交
  （`e5c21bd`→`b115e4d`，web-043~048，由我手工合并 main）。
  **开工先 `git status` + `git log --oneline -3` 确认 main 是否已合并，再从 main 切新分支**
  （如 `feature/public-net`），不直接提交 main，我验收后手工合并。
- 测试基线（feature/qa 实测）：`python -m pytest tests/ -q` = **765 passed**；
  `cd frontend && npx vitest run` = **74 passed**。任何改动后必须保持全绿。
- 记账约定：提交前缀 `feat(web):`/`fix(web):`；注释与测试标签 `web-xxx`
  （已用到 **web-048**，本轮从 **web-049** 起）；每轮结束更新 README 变更日志 +
  `code_review_report_v3.md` 累加。
- **gitignore 陷阱**：`tests/`、`frontend/`、`docs/`、`data/` 被 ignore 但历史文件是
  force-add 跟踪的——新增这些目录下的文件须 `git add -f`。

## 2. 硬约束（红线，逐条遵守）

1. **冻结区零改动**：`src/` 全部内核、`app.py`、`.env`——除非我逐项明确批准
   （上一窗口已两次批准内核改动：web-044 本地大模型、web-048 唤醒窗口，均单点完成）。
   薄层 `kiosk_server/` 与 `frontend/` 是可动区。
2. `data/front_ui/` 只读；密钥（XFYUN/DashScope/vLLM）只在服务端，永不进前端、永不打印。
3. TDD：先写失败测试再实现；外部 API 一律 mock；真实 API 仅用于冒烟脚本验证。
4. Gradio 6.22 依赖钉：`starlette<1.4` + `fastapi<1.0`。
5. LLM 双通道（web-044 起）：`.env LLM_PROVIDER=dashscope|local`（百炼/本地 vLLM 并存可切换），
   本地模式联网搜索失效、embedding/rerank/ASR/TTS/OCR 仍走云端。

## 3. 部署现状事实（上一窗口实证，勿再重复排查）

- **服务器 ub-server（10.0.2.200）**：`kiosk_server` 跑 **7862**（7861 被 Langchain-Chatchat
  占用，不可动）；vLLM Qwen2.5-14B 在 `127.0.0.1:18081`；systemd 单元 `kiosk-server`
  （模板已按实录修正，见 `deploy/server/kiosk-server.service`）；
  conda 环境 `/data/conda_envs/cultural-relics-rag`；代码 `/data/codes/rag_chat`。
- **开发机 → 服务器只有 SSH 隧道**（防火墙只放 22，`ub-server` 主机名开发机不解析）：
  `ssh -L 7862:127.0.0.1:7862 root@10.0.2.200`（体验/冒烟前先开隧道）。
- 前端 env：`.env.development=http://127.0.0.1:7862`（经隧道）；
  `.env.production=http://ub-server:7862`（场馆局域网一体机用）。
- **运维手册：`deploy/OPERATIONS.md`**（三端启停、LLM 双通道切换、隧道联调、故障速查、
  一页纸命令卡片）——启停/切换问题先查它，不要重新发明流程。
- 服务器当前 `LLM_PROVIDER` 与最新代码同步状态由我（用户）手工维护；如需改动先问我。

## 4. 公网部署已定稿计划（上一窗口产出，直接执行）

**差距与既有件（已核实）**：

- token 基础设施**服务端已就绪**：`KIOSK_API_TOKEN` 配置后 `/api/*`（除 `/api/health`）
  校验 `X-Kiosk-Token` 头，WS 校验 `?token=` query（`kiosk_server/app.py`、`voice_ws.py`）；
  `VoiceWsClient` 已支持 token 拼接且 `VITE_API_URL` 的 `https://` 自动推导 `wss://`。
- **前端缺口（本轮主要代码工作，3 处）**：①`client.ts` fetch 未附 token 头；
  ②`useVoiceSession.ts` 未把 token 传给 `VoiceWsClient`；③无 `VITE_API_TOKEN` 变量。
- 冒烟脚本 `scripts/smoke_kiosk_ws.py` / `smoke_kiosk_voice.py` 暂无 `--token` 参数，需小改。
- 带宽：每机峰值 ≈ 下行 PCM 48KB/s + 上行 32KB/s ≈ 0.65Mbps；并发播报上限 =
  服务器公网上行 ÷ 48KB/s（10Mbps≈26 路）。规模大了再做 AAC/Opus 演进
  （协议 `format` 字段已预留，非本轮）。

**三个决策点（开工必须先与我拍板）**：

- **A 公网出口**：A1 公网 IP+端口映射（有固定公网 IP 时首选，注意宽带常封 80/443→用非标端口）；
  A2 云 VPS + frp 反向隧道（无公网 IP 时最稳，带宽受 VPS 限）；A3 Cloudflare Tunnel（免费备选，
  国内稳定性一般）。
- **B 域名与证书**：TLS 必需域名；Caddy 自动签 Let's Encrypt 最省事；
  **域名指向国内 IP/VPS 需 ICP 备案**（A3 不需要）。
- **C 鉴权模式**：起步 = 共享令牌（`KIOSK_API_TOKEN` 全机统一，挡公网扫描/爬虫；
  不防拆机取 token，后续可演进每机一令牌）。

**实施顺序**：①拍板 A/B/C → ②服务端 token + CORS + 反代 TLS（kiosk 退回
`--host 127.0.0.1:7862`、防火墙只放 443/非标端口）并**内网回归** →
③前端 3 处接线（TDD）+ 构建 → ④一台试点机**手机热点**模拟公网跑
`deploy/README.md` §4 验收清单 → ⑤全量下发 + 观察一周（401 量/带宽水位）。

**安全红线（公网特有）**：7862/18081/7861 一律不公网直连（反代背后）；
TLS 全链路；无 token 请求必须 401/拒连；密钥维持服务端不出网。

## 5. 评测与验证工具

- 基线：`python -m pytest tests/ -q`（765）、`cd frontend && npx vitest run`（74）。
- 冒烟：`python scripts/smoke_kiosk_ws.py --port 7862 "问题"`（文本全链）、
  `python scripts/smoke_kiosk_voice.py --port 7862`（语音全链，夹具 CosyVoice 合成）。
- 唤醒延迟探针（上一窗口实测用法）：参考 git 历史 web-048 增补与 /tmp 脚本思路，
  测量「说完→应答开播 Δ」，目标 ≤1s。

## 6. 遗留事项（不属本轮，知悉即可）

- Bug 1（打断后答案显示无关 KB 内容）**挂起**，等服务器日志
  （`journalctl -u kiosk-server --no-pager | grep -E "电影|办证" | tail -20`），勿主动开工。
- 服务器最新代码同步（src/voice_assistant.py、src/llm.py、kiosk_server/tts_clean.py、
  chat.py）由我手工上传，开工前可先问我同步状态。

## 7. 开工暗号

请回复确认：已读 `deploy/OPERATIONS.md`、本简报 §4 计划、`code_review_report_v3.md`
最新增补（web-043~048），并完成 `git status` / 测试基线复测；
然后与我确认 **A/B/C 三个决策点**，再逐项 TDD 实施（不要擅自扩大范围）。
