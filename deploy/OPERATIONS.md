# 一体机问答服务运维手册（启停 + 本地/在线双通道切换）

> 适用：web-044 起。阅读对象：现场运维、开发联调。
> 配套文档：`deploy/README.md`（部署拓扑与首次安装）。
> 本文所有命令按「在哪台机器上执行」分节，**先看机器再抄命令**。

---

## 0. 速览：三端角色、端口、两种模式

### 三端角色

| 端 | 机器 | 跑什么 |
|---|---|---|
| 服务器端 | ub-server（`10.0.2.200`，SSH `root@10.0.2.200`） | 后端 `kiosk_server`（:7862）、本地大模型 vLLM（:18081） |
| 本地端 | Windows 开发机 | 前端开发/构建（vite dev :5173）、SSH 隧道、PC 预览 |
| 一体机端 | 场馆竖屏终端 | 前端构建产物 dist（:8080）+ Chrome kiosk（本轮无需动） |

### 端口表（服务器侧）

| 端口 | 属主 | 说明 |
|---|---|---|
| **7862** | 本项目后端 kiosk_server | 7861 被 Langchain-Chatchat 占用（**不可动**），故用 7862 |
| 7861 | Langchain-Chatchat | 别的项目，**不可动** |
| 18081 | vLLM（Qwen2.5-14B-Instruct-AWQ） | 本地模式的模型服务，OpenAI 兼容接口 |
| 7860 | Gradio 调试台（本项目） | **与 kiosk_server 互斥**（Qdrant 文件锁），一般不开 |

### 两种 LLM 模式（服务器 `.env` 的 `LLM_PROVIDER` 一键切换）

| 模式 | `LLM_PROVIDER` | 问答生成走哪 | 特点 |
|---|---|---|---|
| **在线模式**（默认） | `dashscope` | 阿里云百炼 qwen-plus（云端 API） | 支持联网搜索；按 token 计费；依赖外网 |
| **本地模式** | `local` | 服务器本机 vLLM Qwen2.5-14B | 首字快（实测 ~0.1s）；不计费；**联网搜索失效**（模型自有知识+知识库检索作答） |

> 两种模式**并存**，切换只改 `.env` 一行 + 重启后端，随时可切回。
> 两模式下 embedding/rerank/讯飞 ASR/CosyVoice TTS/手写 OCR **都仍走云端**，
> `DASHSCOPE_API_KEY` 与 `XFYUN_*` 密钥两种模式都需要。

---

## 1. 服务器端操作（root@ub-server）

> 先 SSH：`ssh root@10.0.2.200`，进入 `cd /data/codes/rag_chat`，
> 确认 conda 环境 `(/data/conda_envs/cultural-relics-rag)` 已激活。

### 1.1 启动后端

**方式 A：systemd（推荐，生产常驻）** —— 单元已按本机实录配置好：

```bash
systemctl start kiosk-server        # 启动
systemctl status kiosk-server --no-pager   # 确认 active (running)
```

（若从未装过单元：`cp deploy/server/kiosk-server.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now kiosk-server`，enable 即开机自启。）

**方式 B：前台（调试用，日志直接上屏）**：

```bash
cd /data/codes/rag_chat
python -m kiosk_server --host 0.0.0.0 --port 7862
```

### 1.2 停止 / 重启 / 看日志

| 动作 | systemd 方式 | 前台方式 |
|---|---|---|
| 停止 | `systemctl stop kiosk-server` | 在运行终端按 `Ctrl+C` |
| 重启 | `systemctl restart kiosk-server`（中断几秒，前端自动重连） | Ctrl+C 后重新执行启动命令 |
| 日志 | `journalctl -u kiosk-server -f`（滚动跟踪） | 直接在终端看 |
| 是否活着 | `ss -tlnp \| grep 7862` 有输出；`curl -s http://127.0.0.1:7862/api/health` 返回 JSON | 同左 |

> 若是 `nohup ... &` 方式起的：`ps aux | grep kiosk_server | grep -v grep | awk '{print $2}' | xargs -r kill`。

### 1.3 切换 本地模式 ↔ 在线模式（标准三步）

```bash
# ① 改配置：编辑 /data/codes/rag_chat/.env 中的一行
#    在线模式：LLM_PROVIDER=dashscope
#    本地模式：LLM_PROVIDER=local

# ② 重启后端（必须重启——配置只在进程启动时读取）
systemctl restart kiosk-server       # 前台方式则 Ctrl+C 重起

# ③ 验证（见 1.4）
```

切换**只影响本项目这一个进程**：不装/不卸任何软件，不碰 Langchain-Chatchat、
ComfyUI、vLLM 的进程与配置；`.env` 是本项目私有文件，其他项目读不到。

### 1.4 健康检查与冒烟

```bash
# 服务健康（轻量）
curl -s http://127.0.0.1:7862/api/health

# 全链冒烟（真实走一问：文本+音频，约 40s）
python scripts/smoke_kiosk_ws.py --port 7862 "家博会几点开门？"
#   期望尾行输出 SMOKE_KIOSK_WS_OK

# LLM 层冒烟（验证当前 .env 指定的通道本身）
python scripts/smoke_local_llm.py
#   期望尾行输出 == 冒烟通过 ==；dashscope 通道工厂显示 BailianLLM，local 通道显示 LocalOpenAILLM
```

**本地模式额外前置检查**（vLLM 必须在线，否则问答报错）：

```bash
curl -s http://127.0.0.1:18081/v1/models -H "Authorization: Bearer <LOCAL_LLM_API_KEY>" | head -c 100
# 期望返回含 "qwen25-14b" 的 JSON；无响应则先恢复 vLLM 服务（不属于本项目管理）
```

> vLLM 恢复后**无需重启** kiosk_server——每次问答独立发 HTTP 请求，下一问自然成功。

### 1.5 服务器端注意事项

1. **Gradio 互斥**：要在服务器开 Gradio 调试台（app.py :7860）前，先
   `systemctl stop kiosk-server`；用完 `systemctl start kiosk-server` 启回（Qdrant 文件锁，同库只能一个进程持有）。
2. **7861 不可占**：被 Langchain-Chatchat 使用，本项目固定用 7862。
3. **密钥只在服务器**：`.env` 不进 git、不进前端、不打印到日志。

---

## 2. 本地端操作（Windows 开发机）

> 本机与服务器之间**唯一通道是 SSH 隧道**（服务器防火墙只放行 22 端口，
> `ub-server` 主机名在本机也不解析）。所以**先开隧道，再谈前端**。

### 2.1 SSH 隧道（本机体验服务器后端的前提）

```bash
# 新开一个终端窗口执行，保持窗口不关（关=断）
ssh -L 7862:127.0.0.1:7862 root@10.0.2.200
```

- 已有一条 vLLM 隧道（18081）的话，下次可合并为一条：
  `ssh -L 18081:127.0.0.1:18081 -L 7862:127.0.0.1:7862 root@10.0.2.200`
- **停止隧道**：在该窗口 `Ctrl+C` 或直接关窗。
- **验证隧道通了**：`curl http://127.0.0.1:7862/api/health` 返回 JSON。

### 2.2 前端 dev 模式（本机体验，推荐）

配置（已入库）：`frontend/.env.development` = `VITE_API_URL=http://127.0.0.1:7862`
（经隧道到服务器后端；vite dev 会把 /api、/ws 代理过去，WebSocket 走隧道没问题）

```bash
# 启动（前置：隧道已开 + 服务器后端已起）
cd frontend && npm run dev          # 前端 dev 服务 :5173

# 打开预览（另起动作）：双击 deploy/kiosk/start-pc-preview.bat
#   （默认打开 http://localhost:5173，540×960 竖屏应用窗，免麦克风授权弹窗）
```

| 动作 | 操作 |
|---|---|
| 停止预览 | 关掉预览窗口 |
| 停止 dev 服务 | 在 `npm run dev` 终端 `Ctrl+C` |
| **改了 `.env.development` 后** | **必须重启 `npm run dev`**（vite 只在启动时读 env） |

### 2.3 前端构建版（产物用于一体机部署）

```bash
cd frontend && npm run build        # 产物 frontend/dist（.env.production 生效）
```

- `frontend/.env.production` = `VITE_API_URL=http://ub-server:7862`
  ——面向**场馆局域网**：一体机与服务器同网、主机名可解析、端口互通，**不要改成 127.0.0.1**。
- 本机若想预览构建版：`python deploy/kiosk/serve-dist.py --port 8080`，
  再 `deploy/kiosk/start-pc-preview.bat http://localhost:8080`
  （注意：构建版里写的是 `ub-server:7862`，本机解析不到 → 本机预览请用 2.2 的 dev 模式）。
- 前端测试：`cd frontend && npx vitest run`（开发自检用，非服务）。

### 2.4 附录：本机直连模式（完全不走服务器，可选）

仅当服务器不可用、要在本机独立调试后端时：

```bash
# 本机起后端（与服务器互斥的是 Qdrant 文件锁——本机用自己的 data/，无冲突；
# 但不要和本机 Gradio:7860 同时跑）
python -m kiosk_server --host 127.0.0.1 --port 7861
# frontend/.env.development 临时改回 http://127.0.0.1:7861，重启 npm run dev
```

本机要用本地模式也行：`.env` 设 `LLM_PROVIDER=local`，vLLM 走已有 18081 隧道。

---

## 3. 一体机端（生产终端，简版）

- 启动：`deploy/kiosk/start-kiosk.bat`（可放 `shell:startup` 自启）——
  起 `serve-dist.py :8080` 静态伺服 + Chrome `--kiosk http://127.0.0.1:8080/`。
- 停止：关 Chrome 与 serve-dist 窗口；隐藏菜单（左上角连点 3 次）里有「退出」。
- 一体机访问的是**局域网直连** `ub-server:7862`，不需要 SSH 隧道。
- 本轮前端零改动，一体机端无需任何操作。

---

## 4. 双通道行为差异速查（体验对照）

| 体验点 | 在线模式（dashscope） | 本地模式（local） |
|---|---|---|
| 首文本/首音频 | 检索+云端生成（实测首文本 1.8~3.9s） | 明显更快（本地 14B 首字 ~0.1s） |
| 知识库外问题 | 联网搜索作答（时效信息准） | 模型自有知识作答（无实时信息） |
| 答案人设 | 内核路径仍有「小虎/家博会」残留（冻结内核，已知 backlog） | 同左；但薄层兜底路径为湘小图 |
| 成本 | 按 token 计费 | 零 API 费用（占服务器 GPU） |
| 依赖 | 外网 + DashScope 额度 | 本机 vLLM 必须在线 |

> 两模式共用的不变量：知识库检索/rerank、ASR/TTS、薄层兜底提示词（湘小图/口语化/320 字限长）、
> 前端交互与打断逻辑。

---

## 5. 常见故障速查

| 现象 | 先查 | 处置 |
|---|---|---|
| 前端无预设问题、提问无反应 | 本机 `curl http://127.0.0.1:7862/api/health` | 不通 → 查隧道（2.1）→ 查服务器后端（1.2 是否活着） |
| 改过 `.env*` 没生效 | dev 服务是否重启 | Ctrl+C 重跑 `npm run dev`（vite 只在启动时读 env） |
| 改了 `LLM_PROVIDER` 没生效 | 后端是否重启 | `systemctl restart kiosk-server`（配置只在启动时读） |
| 本地模式问答报错 | vLLM 是否在线（1.4 前置检查） | 恢复 vLLM；恢复后无需重启后端 |
| 后端起不来、日志报 Qdrant 锁 | 7860 Gradio 是否在跑 | 停 Gradio 再启后端（1.5-1） |
| 7862 bind 失败 | `ss -tlnp \| grep 7862` 被谁占 | 杀掉旧 kiosk_server 进程；勿碰 7861（Chatchat） |
| 服务器改了代码没效果 | 是否忘了 `pip install "openai>=1.55,<2"` | 补装依赖后重启 |

---

## 6. 一页纸命令卡片

```bash
# ===== 服务器（ssh root@10.0.2.200, cd /data/codes/rag_chat）=====
systemctl start|stop|restart|status kiosk-server     # 启/停/重启/状态
journalctl -u kiosk-server -f                        # 日志
curl -s http://127.0.0.1:7862/api/health             # 健康
python scripts/smoke_kiosk_ws.py --port 7862 "问题"   # 全链冒烟
python scripts/smoke_local_llm.py                    # LLM 通道冒烟
# 切模式：改 .env 的 LLM_PROVIDER=dashscope|local → systemctl restart kiosk-server → 冒烟

# ===== 本地端（Windows）=====
ssh -L 7862:127.0.0.1:7862 root@10.0.2.200           # 隧道（保持窗口）
cd frontend && npm run dev                           # dev 服务 :5173（改 env 必重启）
# 双击 deploy/kiosk/start-pc-preview.bat             # PC 竖屏预览
```
