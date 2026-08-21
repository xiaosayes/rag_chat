# 数字人一体机部署指南（feat(web) 轮，web-027）

> 适用：竖屏一体机（Windows + Chrome）/ 大屏终端 + 后端服务器（ub-server）。
> 拓扑：**渲染与采集在端，智能在云（经服务器中转）**；密钥只在服务器。
> **日常启停与本地/在线双通道切换 → 见 `deploy/OPERATIONS.md`（web-044 起）。**

## 1. 组件与端口

| 组件 | 位置 | 端口 | 说明 |
|---|---|---|---|
| `frontend/dist` 静态前端 | 一体机本地 | 8080（`serve-dist.py`） | Chrome kiosk 全屏加载 |
| `kiosk_server` 薄层 API | 服务器 | **7861**（ub-server 实例用 **7862**，7861 被 Langchain-Chatchat 占用） | `WS /ws/voice` + `GET /api/*` + `POST /api/ocr` |
| Gradio Web UI（调试用） | 服务器 | 7860 | **与 kiosk_server 互斥**（Qdrant 本地锁，已实证） |
| 讯飞 IAT / 百炼 | 云端 | — | 仅服务器持有密钥 |

## 2. 服务器侧

```bash
cd /data/codes/rag_chat
conda activate cultural-relics-rag
python -m kiosk_server --host 0.0.0.0 --port 7862     # 前台（端口以实际空闲为准）
# 或 systemd：sudo cp deploy/server/kiosk-server.service /etc/systemd/system/
#   （模板已按 ub-server 实录修正 conda 路径与 7862 端口，见文件头注释）
#   sudo systemctl enable --now kiosk-server
```

**互斥手册**：Qdrant 本地嵌入模式对存储目录持独占文件锁（两个进程不能同时持有同一知识库，已实证）。
生产只跑 `kiosk_server`；需要 Gradio 界面调试时先 `systemctl stop kiosk-server`，用完再启回。
（可选增强：本地起 Qdrant 服务、两进程走远程模式——内核原生支持，默认不启用。）

**关键环境变量**（`.env`，均不进前端）：
- 既有：`DASHSCOPE_API_KEY`、`XFYUN_*`、`TTS_*`、`ASR_*`……
- 本轮：`ASR_WAKE_WORDS=你好湘小图`、`ASR_WAKE_GREETING=您好，请问有什么可以帮您？`
- 薄层（可选）：`KIOSK_API_TOKEN`（设置后前端须带 `?token=`）、`KIOSK_CORS_ORIGINS`、
  `KIOSK_OCR_MODEL`（默认 qwen-vl-ocr-latest）、`KIOSK_PRESETS_PATH`（默认 data/kiosk/preset_questions.json）、
  `KIOSK_PROJECT_ID`（默认 jiabohui）
- LLM 双通道（web-044）：`LLM_PROVIDER=dashscope|local`（默认 dashscope）、
  `LOCAL_LLM_BASE_URL`（ub-server 本机 vLLM `http://127.0.0.1:18081/v1`）、
  `LOCAL_LLM_API_KEY`、`LOCAL_LLM_MODEL=qwen25-14b`、可选 `LOCAL_LLM_CONTEXT_TOKENS=4096`；
  切 local 后联网搜索失效（模型自有知识+知识库检索作答），embedding/rerank/ASR/TTS/OCR 仍走云端

**预设问题**：服务器编辑 `data/kiosk/preset_questions.json`（`{"questions": [...]}`）即生效，前端随机抽 8 条展示。

## 3. 一体机侧（Windows）

```bat
REM 一次性：构建前端（开发机执行，把 frontend/dist 拷到一体机）
cd frontend && npm run build        REM .env.production 的 VITE_API_URL 指向服务器

REM 一体机启动（deploy/kiosk/start-kiosk.bat，可放 shell:startup 自启）：
REM   1) python deploy/kiosk/serve-dist.py --port 8080     （静态伺服）
REM   2) chrome --kiosk http://127.0.0.1:8080/ --use-fake-ui-for-media-stream ...
```

Chrome 关键参数：`--kiosk`（全屏无边框）、`--use-fake-ui-for-media-stream`（免麦克风授权弹窗，
**免提常开收音必需**）、`--autoplay-policy=no-user-gesture-required`（免手势自动播音）、
`--disable-pinch`（禁双指缩放）、`--incognito`（无恢复气泡）。

### 3.1 PC 竖屏预览（联调/演示用，web-032/033）

双击 `deploy/kiosk/start-pc-preview.bat`：540×960（9:16，恰为设计稿 0.5 倍）应用窗，
免麦弹窗 + 自动播放；可 `start-pc-preview.bat http://主机:端口` 指定前端地址。
页面为 1080×1920 设计坐标等比缩放（web-034），任意窗口比例均不变形（非 9:16 时两侧留边）。
脚本找 Chrome 失败时自动回退 Edge。

## 4. 验收清单（现场）

1. 启动页进度 0→100 后进首页，小鹿待机动作循环；
2. 说「你好，湘小图」→ ~0.3s 应答「您好，请问有什么可以帮您？」→ 状态行进倾听态；
3. 直接说问题 → 边说边上屏 → 说完 2s 自动提交 → 流式回答 + 语音播报（首音 ~1s）；
4. 播报中说话或点按胶囊 → 立即打断并可继续提问（免唤醒）；
5. 点键盘钮 → 全拼键盘（候选条）/手写板（停笔 2s 识别上屏）→ 发送走同一问答链；
6. 回答气泡下 MusicBar 可重播/暂停/点击 seek（端侧缓存，零网络）；
7. 150s 无操作回首页；300s 无操作自刷新；左上角连点 3 次 → 刷新/退出。

### 4.1 故事绘本专项（web-050 起）

1. 点预设「给我讲个嫦娥奔月的故事」（或语音直接说「给我讲一个〈主题〉的故事」）→
   「湘小图正在想故事…」盖层（**此阶段可点左上角「返回」取消**，不应弹回故事态）；
2. 准备完成自动开播第 1 页：插图位先占位动画、就绪后原位淡入；页底文本 ≤80 字 + 语音讲解；
3. 一页播完**自动翻页**；随时手动点「上一页/下一页」→ 页面立即翻转且播报**立即切换**到新页；
4. 页码指示 `n / 8~10`；末页播尽 → 收尾语「故事讲完啦，还想听什么故事吗？」+ 停留收尾页；
5. 收尾页点「返回」或 150s 无操作 → 回首页；中途点「返回」= 立即静音 + 取消 + 回首页；
6. **播放全程说话不触发任何动作**（无语音打断）；想提问须等讲完或先返回首页；
7. **同名故事再讲一次** → 秒级开播（缓存命中，无准备等待）；
8. 降级：插图个别失败 → 该页占位图照常播讲不阻塞；敏感主题 → 「这个故事我不太会讲，换一个试试吧」停留 ~2.5s 回首页；
9. 讲完一个后说「再讲一个后羿射日的故事」→ 直接开新绘本（带主题即生效）。

## 5. 故障排查

| 现象 | 排查 |
|---|---|
| 启动页卡住 | 一体机无网无碍（资产全本地）；查 `serve-dist.py` 是否起来 |
| 一直「重连中」 | 服务器 `systemctl status kiosk-server`；一体机到 7861 的网络；token 是否配置一致 |
| 唤醒无反应 | 服务器日志看 VAD/ASR；`GET /api/health` 的 `vad` 字段应 ready |
| 有文字无声音 | Chrome 是否带 `--autoplay-policy=no-user-gesture-required`；`health.tts` 应 true |
| 手写识别失败 | `POST /api/ocr` 直连测试；DASHSCOPE_API_KEY 额度 |
| 故事无插图只有占位 | 服务器日志看插图生成（额度/RPM）；`data/story/<sid>/` 下 page_*.png 是否落盘 |
| 讲故事进了普通问答 | 意图句式不符（需含「讲/说…故事」）；服务器日志确认 story_preparing 是否发出 |
| 故事讲到一半不回首页 | 属预期（播放中事件持续复位空闲计时）；讲完收尾页 150s 无操作自然回首页 |
