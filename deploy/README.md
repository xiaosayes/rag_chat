# 数字人一体机部署指南（feat(web) 轮，web-027）

> 适用：竖屏一体机（Windows + Chrome）/ 大屏终端 + 后端服务器（ub-server）。
> 拓扑：**渲染与采集在端，智能在云（经服务器中转）**；密钥只在服务器。

## 1. 组件与端口

| 组件 | 位置 | 端口 | 说明 |
|---|---|---|---|
| `frontend/dist` 静态前端 | 一体机本地 | 8080（`serve-dist.py`） | Chrome kiosk 全屏加载 |
| `kiosk_server` 薄层 API | 服务器 | **7861** | `WS /ws/voice` + `GET /api/*` + `POST /api/ocr` |
| Gradio Web UI（调试用） | 服务器 | 7860 | **与 kiosk_server 互斥**（Qdrant 本地锁，已实证） |
| 讯飞 IAT / 百炼 | 云端 | — | 仅服务器持有密钥 |

## 2. 服务器侧

```bash
cd /data/codes/rag_chat
conda activate cultural-relics-rag
python -m kiosk_server --host 0.0.0.0 --port 7861     # 前台
# 或 systemd：sudo cp deploy/server/kiosk-server.service /etc/systemd/system/
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

## 5. 故障排查

| 现象 | 排查 |
|---|---|
| 启动页卡住 | 一体机无网无碍（资产全本地）；查 `serve-dist.py` 是否起来 |
| 一直「重连中」 | 服务器 `systemctl status kiosk-server`；一体机到 7861 的网络；token 是否配置一致 |
| 唤醒无反应 | 服务器日志看 VAD/ASR；`GET /api/health` 的 `vad` 字段应 ready |
| 有文字无声音 | Chrome 是否带 `--autoplay-policy=no-user-gesture-required`；`health.tts` 应 true |
| 手写识别失败 | `POST /api/ocr` 直连测试；DASHSCOPE_API_KEY 额度 |
