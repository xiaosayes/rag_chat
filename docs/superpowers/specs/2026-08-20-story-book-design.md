# AI 故事绘本设计稿（web-050 起）

> 日期：2026-08-20 ｜ 分支：feature/storybook（== main `5b15c55`，feature/qa 已合并）
> 状态：**设计已获用户拍板**（brainstorming 全程逐项确认），待 spec 过目后进入 writing-plans
> 基线：pytest 772 passed / vitest 74 passed（开工复测全绿）

## 0. 一句话目标

用户对一体机说「给我讲一个〈任意主题〉的故事」→ 一体机呈现一套完整故事绘本：
**翻页式图文（8~10 页，一页 = 一图 + ≤80 字一段文）+ 逐页语音讲解**，
服务端（ub-server）承担全部生成，一体机端只做渲染与交互。

## 1. 锁定的设计决策（用户逐项拍板，逐条可溯源）

| # | 决策点 | 拍板结论 |
|---|---|---|
| D1 呈现形态 | §4.1 | **翻页式**；每故事 **8~10 页**；一页 = 一幅插图 + 一段文字 |
| D2 文本生成 | §4.2 | **分镜脚本先行**：LLM 一次产出全部分镜文字，每镜头 **≤80 字**；模型 **qwen-plus 固定云端**；**儿童/少儿适宜风格**（湘小图讲故事人设）；绘本流程**独立限长**（先例 `FALLBACK_MAX_TOKENS`），与问答 320 tokens（web-041）互不影响 |
| D3 插图生成 | §4.3 | **qwen-image-3.0**（`MultiModalConversation` messages 格式），**严格遵循分镜文字不过度发挥**、跨图高一致性；`prompt_extend=False`、`size=1024*1024`、**异步并发 ≤4**（平台 RPM=20）；key 复用 `.env DASHSCOPE_API_KEY`，永不进前端 |
| D4 local 兼容 | §4.4 | 绘本**全流程固定云端**（文本 + 插图都走百炼），与 `LLM_PROVIDER` 完全解耦；local 模式下 DashScope key 本就在（embedding/rerank 依赖），无新前置 |
| D5 意图路由 | §4.5 | **薄层正则拦截**（`讲/说 + …故事/绘本`，提取主题），命中进绘本、不命中走原问答——宁漏勿抢；预设池加引导入口（预设=提交文本，零新机制）；**不做**「再讲一个」专属状态（无主题→兜底反问；带主题→正则捕获新绘本） |
| D6 播报协同 | §4.6 | **复用问答 TTS 播报管线**（BroadcastSession：句边界喂入/首播地板 12 字/web-046 清洗/web-040 看门狗/打断串行化）；`story_begin` 后**自动开播第 1 页**；**播完一页自动翻页播下一页**；**随时手动上一页/下一页，翻页即切播报** |
| D7 打断/退出 | §4.1 收尾 | 讲完停留结尾页 + 收尾语（上屏+TTS），**不自动跳走**（复用空闲计时 150s 回首页）；中途「返回」= 本地立即静音 + 服务端取消（停 TTS + 取消未完成插图）+ 回首页（对齐 web-047）；**绘本播放全程不支持语音打断**——说话不触发任何动作，提问须等播报完（或先返回首页） |
| D8 成本与安全 | §4.7 | **同名故事缓存**：主题归一化（去首尾空白、去标点）后作 key，命中跳 LLM 跳插图秒开直播；缓存容量 **500MB LRU**（整故事淘汰）；超时：脚本 **60s** / 单图 **90s** / 总预算 **300s**；单图失败**重试 1 次**→仍失败该页占位图照常播；内容审核拦截 → 湘小图礼貌拒讲并退出绘本态 |
| D9 图片存储 | 补充 | **服务端落盘** `data/story/<story_id>/page_<n>.png` + 薄层供图 `GET /api/story/<id>/img/<n>`；前端不碰 OSS 临时直链（约 24h 过期，且一体机只保证到 ub-server 局域网连通）；`data/` 本已 gitignore，运行时产物不污染仓库 |
| D10 架构拓扑 | 方案拍板 | **方案 A：WS 单通道扩展**（否决 REST 控制面混合、独立 WS 通道）；播报音频与翻页事件严格同通道排序 |

## 2. 实证事实（设计依据，真实 API 实测 2026-08-20）

- `qwen-image-3.0` **必须走 multimodal-generation messages 格式**（SDK `MultiModalConversation.call`）；老 `ImageSynthesis` 任务式 API 报 400 InvalidParameter（模型名无效）。
- 速度杠杆实测（同 prompt「儿童绘本插画：一只可爱的卡通小鹿站在森林里」）：
  `prompt_extend=True`（平台默认）**71.0s** → `prompt_extend=False` **17.9s**
  → 再 `size=1024*1024` **12.9s**。默认尺寸实测 2048×2048。
  `prompt_extend=False` 同时是「严格遵循文字、不过度发挥」的语义保证（无 LLM 改写）。
- 响应结构：`output.choices[0].message.content = [{"image": "<OSS URL>"}]`。
- 平台限额标注 **RPM=20**；出图质量目检达标（儿童绘本风，严格切题，留档 `/tmp/probe_qi3_noextend.png`）。
- `qwen-image` / `qwen-image-plus` / `wanx2.1-t2i-turbo` 亦可用（9.3s/9.3s/13.7s），已拍板不选。

## 3. 模块边界

**新增 4 文件 + 薄改 5 文件；冻结区（`src/`、`app.py`、`.env`）零触碰。**

| 文件 | 性质 | 职责 |
|---|---|---|
| `kiosk_server/story.py` | 新增 | `StorySession` 总编排（意图命中→脚本→并发插图→逐页播报驱动→状态事件）；`StoryCache`（落盘 + 主题归一化 key + 500MB LRU）；`ScriptClient`（qwen-plus，`STORY_SCRIPT_MAX_TOKENS=1600`）；`ImageClient`（qwen-image-3.0）；`STORY_INTENT` 正则 |
| `kiosk_server/voice.py` | 薄改 | `VoiceSession.set_story_mode(on)`：故事态**丢弃所有上行音频帧**（唤醒/ASR/语音打断全静默）；`ask()` 入口先过故事正则——命中转 StorySession、不命中走原问答（语音 ASR 定稿与键盘文本同一 funnel，单点拦截） |
| `kiosk_server/voice_ws.py` | 薄改 | WS 路由增 `story_page`/`story_finish`/`story_cancel`；故事态 `barge_in`/`ask` 防御处置（§6） |
| `kiosk_server/app.py` | 薄改 | `GET /api/story/<id>/img/<n>` 供图（FileResponse；未就绪 404；token 走 `?token=` 查询参数——浏览器 `<img>` 无自定义头，与 WS token 同先例） |
| `kiosk_server/config.py` | 薄改 | `KIOSK_STORY_*` 配置族（§5 默认值） |
| `frontend/` | 新增为主 | `StoryBook.vue`（绘本页组件）、`useStorySession.ts`（状态/翻页/自动推进）、`PcmPlayer` 增 `onDrain` 回调（`onended` 链已具备）、`VoiceWsClient` 增 3 个发送方法（事件本就泛型透传零改动）、store 增 `story` 模式 |

**播报复用策略（核心决策）**：不重写 TTS 编排——StorySession 持有**专用 BroadcastSession 实例**，
其 pipeline 为 `StoryPagePipeline`（`query_stream` 直接 yield 当页 ≤80 字文本，一两个 chunk）。
句边界喂入/首播地板/web-046 清洗/web-040 看门狗/web-029 打断串行化全部原样继承。
`answer_*` 事件由 StorySession 的 emit 包装器改名/抑制为 `story_speak_*`；
问答历史留在问答实例，故事实例历史被 story pipeline 忽略，互不污染。

## 4. WS 协议（单通道扩展）

**上行新增**：

```json
{"type":"story_page","n":3}        // 手动翻页与自动推进同一消息（客户端主导页码）
{"type":"story_finish"}            // 末页播尽
{"type":"story_cancel"}            // 返回退出
```

故事触发零新入口：`ask` 文本被服务端正则拦截（如「给我讲一个霸王别姬的故事」→ 主题"霸王别姬"）。

**下行新增**：

```json
{"type":"story_preparing","theme":"霸王别姬"}
{"type":"story_begin","story_id":"…","title":"…","total":9,"cached":false,
 "pages":[{"n":1,"text":"…"}, …]}                       // 文本全量下发，永远就绪
{"type":"story_page_img","n":1,"url":"/api/story/…/img/1"}
{"type":"story_speak_start","n":1}                      // 音频帧仍走二进制 PCM 下行
{"type":"story_speak_end","n":1,"cancelled":false}
{"type":"story_end","reason":"done|cancelled|error"}
{"type":"story_error","code":"moderation|script_failed|…","message":"…"}
```

**翻页与播报同步状态机（客户端主导页码，服务端反应式）**：

1. `story_begin` 后服务端自动开播第 1 页；
2. 页 n 音频发完 → `story_speak_end{n}` → 前端 `PcmPlayer.onDrain`（真实播尽）→
   非末页：UI 翻页 + 发 `story_page{n+1}`；末页：发 `story_finish` →
   服务端播收尾语 → `story_end{done}`（收尾页停留，空闲计时回首页）；
3. 手动翻页：UI 立即翻转（乐观）+ 发 `story_page{m}` → 服务端 web-029 同款串行化
   barge 当前页、改播第 m 页；
4. 页 = 文本（全量已下发）+ 插图（异步）——**翻页不等图**，`story_page_img` 到达前显占位、
   到达后原位淡入。

## 5. 生成与 Prompt 设计

**分镜脚本（qwen-plus，单轮非流式）**：要求输出 JSON
`{"title":str, "characters":str, "scenes":[str,…]}`——
`characters` 为 LLM 自提炼的**角色形象锚定描述**（年龄感/发型/服饰），供每张插图 prompt 重复携带；
`scenes` 8~10 段、每段 ≤80 字、儿童语气（亲切讲故事的姐姐、口语化、无列表无 Markdown、健康适龄）。
校验失败（非 JSON / 段数越界 / 单段超长）→ 带修正意见**重试 1 次** →
仍越界则确定性钳制（句边界截 80 字、段数切 10）；**<6 段判失败**走 `story_error`。

**插图 prompt 模板（每张拼装）**：

```
风格锚：中国传统绘本插画，水彩淡彩，柔和温暖，儿童读物风格，画面简洁
+ characters（每张重复——跨图一致性关键）
+ 当页分镜原文（严格遵循；prompt_extend=False 防改写）
+ 负向约束：画面中不要出现任何文字、水印、标志；不要恐怖/阴暗元素
```

实现期用冒烟脚本 A/B 实测调优后定稿（留档对比图为证）。

**配置默认值（`KIOSK_STORY_*`，运维可调）**：
页数 8~10；脚本 `qwen-plus` / 1600 tokens / 60s 超时；
插图 `qwen-image-3.0` / `1024*1024` / 并发 4 / 单张 90s / 总预算 300s；
缓存 `data/story/` / 500MB LRU；收尾语「故事讲完啦，还想听什么故事吗？」。

## 6. 异常与降级矩阵

| 情形 | 行为 |
|---|---|
| 同名缓存命中 | 跳 LLM 跳插图，秒级 `story_begin{cached:true}` 直接开播；LRU 按整故事淘汰 |
| 脚本失败/审核拒答 | `story_error{moderation\|script_failed}` + 湘小图话术，退回非故事态 |
| 单图失败 | 重试 1 次 → 仍失败：该页占位图照常播讲 |
| 总预算 300s 超时 | 已就绪页正常用，未就绪页占位，不阻塞播报 |
| 故事态收到 `ask`（防御） | 自动 `story_cancel` 后照常作答（前端本不该发） |
| 故事态收到 `barge_in` | 忽略（记日志） |
| `story_cancel` | 停播 + 取消未完成插图任务 + `story_end{cancelled}` + 回首页 |
| WS 断连 | StorySession 随会话 close 全量取消（线程/任务/TTS 句柄） |

## 7. 测试策略（TDD，外部 API 全 mock）

- **pytest** `tests/web050_story_*.py`：意图正则命中/漏判；脚本解析/校验/重试/钳制；
  缓存命中/未中/LRU/容量账；插图编排（并发≤4/超时/重试/占位/页序优先）；
  StorySession 全流程（假 TTS 假 LLM：begin→逐图→逐页 speak→翻页 barge 串行化→finish→cancel）；
  故事态语音静默（帧丢弃/submit 不入/唤醒不答）；`ask` 防御；供图端点 200/404/token。
- **vitest**：`StoryBook.vue`（渲染/翻页边界/占位→图淡入/结束态/返回发 cancel）、
  `useStorySession`（事件流转/onDrain 自动推进/乐观翻页）、`PcmPlayer.onDrain`、
  `VoiceWsClient` 新方法、store 模式切换。
- **冒烟**（真实 API，留档）：`scripts/smoke_story.py`「霸王别姬」全链——
  脚本 JSON、8~10 张图落盘、逐页耗时、prompt A/B 对比图。
- **记账**：提交前缀 `feat(web):`，测试标签 web-050 起；每轮结束更新 README 变更日志 +
  `code_review_report_v3.md` 累加；`tests/`、`frontend/`、`docs/` 下新文件须 `git add -f`。

## 8. 范围排除（YAGNI，明确不做）

语音翻页指令；逐字高亮；「再听一遍」按钮；「再讲一个」专属状态；
Gradio 侧任何改动；绘本多轮对话；公网部署相关任何事项（另一窗口任务）。
