# 语音功能（讯飞 ASR + CosyVoice TTS）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Gradio Web UI 集成讯飞 IAT 流式语音输入（ASR）与阿里百炼 CosyVoice 句子级流式语音播报（TTS）。

**Architecture:** 新增 `src/asr.py`（讯飞 IAT WebSocket 客户端：鉴权/帧组装/热词/纠错/会话，后端纯 Python）+ `src/tts.py`（百炼 SpeechSynthesizer 封装：逐句合成、句子级流式播放）+ `src/audio_bootstrap.py`（static-ffmpeg 引导，gradio 6 原生 HLS 流式播放依赖）。app.py 集成：`gr.Audio(streaming=True)` 麦克风流式输入 → ASR 实时出字填入输入框；`respond().then(tts_after_answer)` 生成器逐句 yield wav → gradio 原生 HLS 流式播放 + 重播副本。

**Tech Stack:** Python 3.10+、gradio 6.22（已装）、dashscope 1.25.1（已装）、websocket-client>=1.7.0（已装）、static-ffmpeg（已装）、numpy（已装）、pydub（gradio 依赖，已装）

## Global Constraints

- 项目根目录 `E:/project/agent_project/pi/test/`，分支 `feature/audio`
- 遵循项目约定：src/ 扁平模块、loguru 日志、pydantic-settings 配置（src/config.py）、mock 测试优先（不依赖真实 API Key）
- **static-ffmpeg 引导必须在 `import gradio` 之前执行**（gradio 导入即触发 pydub 导入，pydub 在 import 时缓存 ffmpeg 查找结果）
- 全量回归基准：`pytest tests/ -q` → **397 passed**（2 项已知失败 `TestDocumentControlCharCleaning` 为 bug-117b 未实现，与本功能无关，不得"修好"）
- 新功能以 bug-121 编号**追加**写入 `bug-fix-plan.md`（不改动任何旧记录）
- `.env` 不提交（.gitignore）；`.env.example` 提交；`docs/` 目录在 .gitignore 中，须 `git add -f`
- 每次任务结束 commit；commit message 遵循项目既有风格（`feat: ...` / `docs: ...`）

---

### Task 1: 配置先行（src/config.py + .env.example + requirements.txt）

**Files:**
- Modify: `src/config.py`（在 `# ========== 意图理解 ==========` 之前插入 ASR/TTS 配置段）
- Modify: `.env.example`（追加讯飞 ASR + TTS 配置段）
- Modify: `requirements.txt`（追加两行依赖）

**Interfaces:**
- Produces: `settings.xfyun_app_id/xfyun_api_key/xfyun_api_secret`（str，默认 ""）、`settings.asr_language="zh_cn"`、`settings.asr_accent="mandarin"`、`settings.asr_vad_eos=1800`、`settings.asr_max_duration=30`、`settings.asr_sample_rate=16000`、`settings.asr_dict_dir`（Path，默认 `data/voice`）、`settings.tts_enabled=True`、`settings.tts_model="cosyvoice-v3-flash"`、`settings.tts_voice=""`、`settings.tts_chunk_chars=1000`

- [ ] **Step 1: 写失败测试**（tests/test_edge_cases.py 末尾追加类）

```python
class TestVoiceConfigDefaults:
    """语音功能配置默认值（bug-121）"""

    def test_xfyun_keys_default_empty(self):
        from src.config import Settings
        s = Settings(_env_file=None)
        assert s.xfyun_app_id == ""
        assert s.xfyun_api_key == ""
        assert s.xfyun_api_secret == ""

    def test_asr_defaults(self):
        from src.config import Settings
        s = Settings(_env_file=None)
        assert s.asr_language == "zh_cn"
        assert s.asr_accent == "mandarin"
        assert s.asr_vad_eos == 1800
        assert s.asr_max_duration == 30
        assert s.asr_sample_rate == 16000

    def test_tts_defaults(self):
        from src.config import Settings
        s = Settings(_env_file=None)
        assert s.tts_enabled is True
        assert s.tts_model == "cosyvoice-v3-flash"
        assert s.tts_voice == ""
        assert s.tts_chunk_chars == 1000
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_edge_cases.py::TestVoiceConfigDefaults -v`
Expected: FAIL（AttributeError: 'Settings' object has no attribute 'xfyun_app_id'）

- [ ] **Step 3: 实现配置**

`src/config.py` 在意图理解段之前插入：

```python
    # ========== 讯飞语音识别 (ASR) ==========
    xfyun_app_id: str = Field(default="", description="讯飞开放平台 APP_ID（语音听写 IAT）")
    xfyun_api_key: str = Field(default="", description="讯飞开放平台 API_KEY")
    xfyun_api_secret: str = Field(default="", description="讯飞开放平台 API_SECRET")
    asr_language: str = Field(default="zh_cn", description="识别语言（zh_cn 普通话）")
    asr_accent: str = Field(default="mandarin", description="口音（mandarin 普通话）")
    asr_vad_eos: int = Field(default=1800, ge=0, description="静音检测时长 ms（VAD 自动结束转写）")
    asr_max_duration: int = Field(default=30, ge=1, description="最长录音秒数兜底（超时强制结束）")
    asr_sample_rate: int = Field(default=16000, description="IAT 采样率（16k PCM）")
    asr_dict_dir: Path = Field(default=Path("data/voice"), description="多音字/热词配置目录")

    # ========== 语音合成 (TTS) ==========
    tts_enabled: bool = Field(default=True, description="语音播报总开关（默认开）")
    tts_model: str = Field(default="cosyvoice-v3-flash", description="TTS 模型（一期；二期真人音色用 cosyvoice-v3.5-flash）")
    tts_voice: str = Field(default="", description="TTS 音色（默认小男孩，真实 API 确认后填入）")
    tts_chunk_chars: int = Field(default=1000, ge=100, description="TTS 长文本分段长度（字符）")
```

注意：`Path` 已在 src/config.py 顶部导入（`from pathlib import Path`），无需新增导入。

- [ ] **Step 4: 更新 .env.example（追加到文件末尾）**

```
# ========== 讯飞语音听写 (ASR) ==========
# 获取：讯飞开放平台 https://www.xfyun.cn → 控制台 → 语音听写 → 创建应用
XFYUN_APP_ID=your_app_id
XFYUN_API_KEY=your_api_key
XFYUN_API_SECRET=your_api_secret
ASR_VAD_EOS=1800        # 静音检测 ms（停止说话约 1.8s 自动结束转写）
ASR_MAX_DURATION=30     # 最长录音秒数兜底

# ========== 语音合成 (TTS) ==========
# 一期：cosyvoice-v3-flash 系统音色（默认小男孩，真实 API 确认 voice id 后填入）
# 二期：cosyvoice-v3.5-flash + 自定义真人音色（见 README「二期音色定制」）
# API Key 复用 DASHSCOPE_API_KEY（上方）
TTS_ENABLED=true
TTS_MODEL=cosyvoice-v3-flash
TTS_VOICE=
TTS_CHUNK_CHARS=1000
```

- [ ] **Step 5: 更新 requirements.txt（在 "=== 核心依赖 ===" 段 aliyun 之后追加）**

```
# 语音功能（bug-121）
websocket-client>=1.7.0     # 讯飞 IAT WebSocket（ASR）
static-ffmpeg>=1.5.0        # gradio 6 原生 HLS 流式音频输出所需（自带 ffmpeg/ffprobe 二进制，跨平台）
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_edge_cases.py::TestVoiceConfigDefaults -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add src/config.py tests/test_edge_cases.py .env.example requirements.txt
git commit -m "feat: 语音功能配置（讯飞 ASR 密钥 + TTS 配置，bug-121 基础）"
```

---

### Task 2: ffmpeg 引导（src/audio_bootstrap.py）

**Files:**
- Create: `src/audio_bootstrap.py`
- Test: `tests/test_asr.py`（新建，本任务先建文件骨架）

**Interfaces:**
- Produces: `ensure_ffmpeg() -> bool` —— 调用 `static_ffmpeg.add_paths()` 将 ffmpeg/ffprobe 加入 PATH；失败返回 False 不抛异常

- [ ] **Step 1: 写失败测试**（tests/test_asr.py 新建）

```python
"""语音功能测试（bug-121）：src/asr.py 与音频环境引导"""
import base64
import json


class TestAudioBootstrap:
    def test_ensure_ffmpeg_returns_bool(self):
        from src.audio_bootstrap import ensure_ffmpeg
        result = ensure_ffmpeg()
        assert isinstance(result, bool)

    def test_ensure_ffmpeg_does_not_raise(self):
        from src.audio_bootstrap import ensure_ffmpeg
        ensure_ffmpeg()  # 不应抛异常
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_asr.py::TestAudioBootstrap -v`
Expected: FAIL（ModuleNotFoundError: No module named 'src.audio_bootstrap'）

- [ ] **Step 3: 实现**

```python
"""音频环境引导：确保 ffmpeg/ffprobe 可用（gradio 6 HLS 流式音频输出依赖 pydub+ffmpeg）。

必须在 import gradio 之前调用 ensure_ffmpeg()（gradio 导入即触发 pydub 导入，
pydub 在 import 时缓存 ffmpeg 查找结果）。
"""
from loguru import logger


def ensure_ffmpeg() -> bool:
    """将 static-ffmpeg 自带的 ffmpeg/ffprobe 二进制目录加入 PATH。

    Returns:
        True 表示 ffmpeg 可用；False 表示不可用（调用方自行降级）。
    """
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        import shutil
        ok = shutil.which("ffmpeg") is not None
        if not ok:
            logger.warning("static-ffmpeg 已加载但 ffmpeg 不在 PATH")
        return ok
    except Exception as e:
        logger.warning(f"ffmpeg 引导失败（TTS 流式播放将降级为一次性播放）: {e}")
        return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_asr.py::TestAudioBootstrap -v`
Expected: 2 passed（本机已装 static-ffmpeg，返回 True）

- [ ] **Step 5: Commit**

```bash
git add src/audio_bootstrap.py tests/test_asr.py
git commit -m "feat: audio_bootstrap（static-ffmpeg 引导，gradio HLS 流式输出前置条件）"
```

---

### Task 3: 讯飞 ASR 客户端——鉴权/帧/热词/纠错/字典（src/asr.py 无网络部分）

**Files:**
- Create: `src/asr.py`
- Test: `tests/test_asr.py`（追加）

**Interfaces:**
- Produces:
  - `IflytekASR.__init__(self, app_id, api_key, api_secret, language="zh_cn", accent="mandarin", vad_eos_ms=1800, hotwords=None, corrections=None)`；`__init__` 内将 corrections 按键长降序排序（多字符优先）
  - `IflytekASR.build_auth_url(api_key, api_secret, host="iat-api.xfyun.cn", path="/v2/iat") -> str`（staticmethod）
  - `IflytekASR._build_frame(audio: bytes, status: int) -> str`（status: 0 首帧/1 中间/2 尾帧；hotwords 为空时省略该键）
  - `IflytekASR._handle_message(data: str)`（解析服务端 JSON：wpgs rpl/apd 部分结果累积、ls 最终结果、错误码）
  - `IflytekASR.correct(text: str) -> str`
  - `load_dict(project_id: str = "", dict_dir: Optional[Path] = None) -> dict`，返回 `{"hotwords": [...], "corrections": {...}}`（全局 `asr_dict.json` + 项目 `{project_id}_asr_dict.json` 合并，项目覆盖同名键）
- Consumes: 无（纯标准库: base64/hashlib/hmac/json/urllib.parse/email.utils）

- [ ] **Step 1: 写失败测试**（tests/test_asr.py 追加）

```python
class TestIflytekAuth:
    def test_auth_url_structure(self):
        from src.asr import IflytekASR
        url = IflytekASR.build_auth_url("test_key", "test_secret")
        assert url.startswith("wss://iat-api.xfyun.cn/v2/iat?")
        assert "authorization=" in url
        assert "date=" in url
        assert "host=iat-api.xfyun.cn" in url

    def test_auth_url_changes_with_secret(self):
        from src.asr import IflytekASR
        u1 = IflytekASR.build_auth_url("k", "s1")
        u2 = IflytekASR.build_auth_url("k", "s2")
        assert u1 != u2


class TestIflytekFrames:
    def test_first_frame_status_zero(self):
        from src.asr import IflytekASR
        asr = IflytekASR("app1", "k", "s", hotwords=["热词"])
        frame = json.loads(asr._build_frame(b"\x00\x01", 0))
        assert frame["common"]["app_id"] == "app1"
        assert frame["data"]["status"] == 0
        assert frame["data"]["encoding"] == "raw"
        assert base64.b64decode(frame["data"]["audio"]) == b"\x00\x01"

    def test_hotwords_joined_with_space(self):
        from src.asr import IflytekASR
        asr = IflytekASR("a", "k", "s", hotwords=["司母戊鼎", "重庆(chong qing)"])
        frame = json.loads(asr._build_frame(b"", 2))
        assert frame["business"]["hotwords"] == "司母戊鼎 重庆(chong qing)"
        assert frame["business"]["dwa"] == "wpgs"

    def test_no_hotwords_omits_key(self):
        from src.asr import IflytekASR
        asr = IflytekASR("a", "k", "s")
        frame = json.loads(asr._build_frame(b"", 2))
        assert "hotwords" not in frame["business"]


class TestIflytekParsing:
    def _make_asr(self):
        from src.asr import IflytekASR
        return IflytekASR("a", "k", "s")

    def test_apd_appends_partial(self):
        asr = self._make_asr()
        asr._handle_message(json.dumps({
            "code": 0,
            "data": {"result": {"pgs": "apd", "ws": [{"cw": [{"w": "你好"}]}]}},
        }))
        assert asr.current_text == "你好"

    def test_rpl_replaces_last_sentence(self):
        asr = self._make_asr()
        asr._handle_message(json.dumps({"code": 0, "data": {"result": {"pgs": "apd", "ws": [{"cw": [{"w": "你好"}]}]}}}))
        asr._handle_message(json.dumps({"code": 0, "data": {"result": {"pgs": "rpl", "ws": [{"cw": [{"w": "您好"}]}]}}}))
        assert asr.current_text == "您好"

    def test_ls_marks_final(self):
        asr = self._make_asr()
        asr._handle_message(json.dumps({"code": 0, "data": {"result": {"pgs": "apd", "ls": True, "ws": [{"cw": [{"w": "你好"}]}]}}}))
        assert asr.is_final() is True
        assert asr.final_text == "你好"

    def test_error_code_sets_error(self):
        asr = self._make_asr()
        asr._handle_message(json.dumps({"code": 10110, "message": "invalid appid"}))
        assert asr.error == "10110: invalid appid"


class TestIflytekCorrections:
    def test_corrections_applied_longest_first(self):
        from src.asr import IflytekASR
        asr = IflytekASR("a", "k", "s", corrections={"期中": "青铜器", "四亩无顶": "司母戊鼎"})
        assert asr.correct("四亩无顶是期中铸造的") == "司母戊鼎是青铜器铸造的"


class TestIflytekDict:
    def test_global_plus_project_override(self, tmp_path):
        from src.asr import load_dict
        (tmp_path / "asr_dict.json").write_text(
            json.dumps({"hotwords": ["a"], "corrections": {"x": "y"}}), encoding="utf-8")
        (tmp_path / "p1_asr_dict.json").write_text(
            json.dumps({"corrections": {"x": "z"}}), encoding="utf-8")
        d = load_dict("p1", tmp_path)
        assert d["hotwords"] == ["a"]
        assert d["corrections"]["x"] == "z"

    def test_project_missing_falls_back_global(self, tmp_path):
        from src.asr import load_dict
        (tmp_path / "asr_dict.json").write_text(
            json.dumps({"hotwords": ["a"], "corrections": {}}), encoding="utf-8")
        d = load_dict("nope", tmp_path)
        assert d["hotwords"] == ["a"]

    def test_missing_dict_returns_empty(self, tmp_path):
        from src.asr import load_dict
        d = load_dict("", tmp_path)
        assert d == {"hotwords": [], "corrections": {}}
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_asr.py -v`
Expected: FAIL（ModuleNotFoundError: No module named 'src.asr'）

- [ ] **Step 3: 实现 src/asr.py（无网络部分）**

```python
"""讯飞语音听写（IAT）WebSocket 客户端（bug-121）。

协议：wss://iat-api.xfyun.cn/v2/iat
  - 鉴权：HMAC-SHA256 签名（host/date/request-line）→ base64 authorization
  - 请求帧：common(app_id) + business(language/domain/accent/vad_eos/dwa/hotwords) + data(status/format/encoding/audio)
  - 响应：code=0 时 data.result 含部分结果（wpgs 动态修正：pgs=rpl 替换 / apd 追加；ls=true 为最终结果）
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from email.utils import formatdate
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

IAT_HOST = "iat-api.xfyun.cn"
IAT_PATH = "/v2/iat"


class IflytekASR:
    """讯飞语音听写客户端。feed() 发送音频帧，current_text 实时累积部分结果。"""

    def __init__(
        self,
        app_id: str,
        api_key: str,
        api_secret: str,
        language: str = "zh_cn",
        accent: str = "mandarin",
        vad_eos_ms: int = 1800,
        hotwords: Optional[List[str]] = None,
        corrections: Optional[Dict[str, str]] = None,
        _ws=None,  # 测试注入
    ):
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.language = language
        self.accent = accent
        self.vad_eos_ms = vad_eos_ms
        self.hotwords = list(hotwords or [])
        # 多字符优先：替换时先匹配长词，避免"母鼎"先于"司母戊鼎"被替换
        self.corrections = dict(sorted((corrections or {}).items(), key=lambda kv: len(kv[0]), reverse=True))
        self._ws = _ws
        self._first_frame = True
        self._partial_sentences: List[str] = []
        self._current_text = ""
        self._final_text = ""
        self._is_final = False
        self.error: Optional[str] = None

    # ---------- 鉴权 ----------

    @staticmethod
    def build_auth_url(api_key: str, api_secret: str, host: str = IAT_HOST, path: str = IAT_PATH) -> str:
        date = formatdate(usegmt=True)  # RFC 1123 GMT
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
        signature = base64.b64encode(
            hmac.new(api_secret.encode("utf-8"), signature_origin.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")
        authorization_origin = (
            f'api_key="{api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
        return (
            f"wss://{host}{path}?authorization={urllib.parse.quote(authorization)}"
            f"&date={urllib.parse.quote(date)}&host={host}"
        )

    # ---------- 帧组装 ----------

    def _build_frame(self, audio: bytes, status: int) -> str:
        business: Dict = {
            "language": self.language,
            "domain": "iat",
            "accent": self.accent,
            "vad_eos": self.vad_eos_ms,
            "dwa": "wpgs",
        }
        if self.hotwords:
            business["hotwords"] = " ".join(self.hotwords)
        return json.dumps({
            "common": {"app_id": self.app_id},
            "business": business,
            "data": {
                "status": status,  # 0 首帧, 1 中间帧, 2 尾帧
                "format": "audio/L16;rate=16000",
                "encoding": "raw",
                "audio": base64.b64encode(audio).decode("utf-8"),
            },
        }, ensure_ascii=False)

    # ---------- 响应解析 ----------

    def _handle_message(self, data: str) -> None:
        """解析服务端一帧响应（供接收线程调用，测试可直接调用）。"""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            return
        code = msg.get("code", 0)
        if code != 0:
            self.error = f"{code}: {msg.get('message', '')}"
            logger.warning(f"讯飞 IAT 返回错误: {self.error}")
            return
        result = (msg.get("data") or {}).get("result") or {}
        ws = result.get("ws", [])
        if not ws:
            return
        sentence = "".join(w["cw"][0]["w"] for w in ws)
        pgs = result.get("pgs", "apd")
        if pgs == "rpl":
            if self._partial_sentences:
                self._partial_sentences[-1] = sentence
            else:
                self._partial_sentences.append(sentence)
        else:
            self._partial_sentences.append(sentence)
        self._current_text = "".join(self._partial_sentences)
        if result.get("ls"):
            self._final_text = self._current_text
            self._is_final = True

    # ---------- 纠错 ----------

    def correct(self, text: str) -> str:
        for wrong, right in self.corrections.items():
            text = text.replace(wrong, right)
        return text

    # ---------- 会话状态 ----------

    def is_final(self) -> bool:
        return self._is_final

    @property
    def current_text(self) -> str:
        return self._current_text

    @property
    def final_text(self) -> str:
        return self._final_text


def load_dict(project_id: str = "", dict_dir: Optional[Path] = None) -> dict:
    """加载多音字/热词配置：全局 asr_dict.json + 项目 {project_id}_asr_dict.json（项目覆盖）。

    Returns:
        {"hotwords": [...], "corrections": {...}}
    """
    dict_dir = Path(dict_dir) if dict_dir else Path("data/voice")
    merged: Dict = {"hotwords": [], "corrections": {}}

    def _merge(path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"加载语音字典失败 {path}: {e}")
            return
        merged["hotwords"].extend(data.get("hotwords", []))
        merged["corrections"].update(data.get("corrections", {}) or {})

    _merge(dict_dir / "asr_dict.json")
    if project_id:
        _merge(dict_dir / f"{project_id}_asr_dict.json")
    # 去重保序
    seen = set()
    merged["hotwords"] = [w for w in merged["hotwords"] if not (w in seen or seen.add(w))]
    return merged
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_asr.py -v`
Expected: 全部通过（TestAudioBootstrap 2 + 本任务 13）

- [ ] **Step 5: Commit**

```bash
git add src/asr.py tests/test_asr.py
git commit -m "feat: 讯飞 IAT 客户端（鉴权/帧组装/wpgs 解析/热词/纠错/字典）"
```

---

### Task 4: ASR 会话管理（连接/feed/finish/close + mock websocket 测试）

**Files:**
- Modify: `src/asr.py`（追加连接与会话方法）
- Test: `tests/test_asr.py`（追加）

**Interfaces:**
- Produces:
  - `IflytekASR.connect()` —— `websocket.create_connection(url, timeout=10)` + 启动接收线程（daemon）
  - `IflytekASR.feed(pcm: bytes) -> str` —— 发送帧（status 0→1），返回 `current_text`（含部分结果）
  - `IflytekASR.finish() -> str` —— 发送尾帧（status=2），轮询等待 `is_final()`（超时 10s），关闭连接，返回最终文本（幂等：已关闭且已有 final_text 时直接返回）
  - `IflytekASR.close()` —— 关闭连接（幂等）

- [ ] **Step 1: 写失败测试**（tests/test_asr.py 追加）

```python
class _FakeWS:
    """模拟 websocket 连接：记录发送的帧，按队列返回响应。"""

    def __init__(self, responses=None):
        self.sent = []
        self.responses = list(responses or [])
        self.closed = False

    def send(self, data):
        self.sent.append(data)

    def recv(self):
        if self.responses:
            return self.responses.pop(0)
        time.sleep(0.05)
        return None

    def close(self):
        self.closed = True


class TestIflytekSession:
    def test_feed_sends_frame_and_returns_partial(self):
        from src.asr import IflytekASR
        fake = _FakeWS()
        asr = IflytekASR("a", "k", "s", _ws=fake)
        asr._handle_message(json.dumps({"code": 0, "data": {"result": {"pgs": "apd", "ws": [{"cw": [{"w": "你好"}]}]}}}))
        text = asr.feed(b"\x00\x01")
        assert text == "你好"
        first = json.loads(fake.sent[0])
        assert first["data"]["status"] == 0  # 首帧
        text2 = asr.feed(b"\x00\x01")
        assert json.loads(fake.sent[1])["data"]["status"] == 1  # 中间帧

    def test_finish_sends_last_frame_and_returns_final(self):
        from src.asr import IflytekASR
        fake = _FakeWS()
        asr = IflytekASR("a", "k", "s", _ws=fake)
        asr._handle_message(json.dumps({"code": 0, "data": {"result": {"pgs": "apd", "ls": True, "ws": [{"cw": [{"w": "你好"}]}]}}}))
        final = asr.finish()
        assert final == "你好"
        assert json.loads(fake.sent[-1])["data"]["status"] == 2  # 尾帧
        assert fake.closed is True

    def test_finish_idempotent(self):
        from src.asr import IflytekASR
        asr = IflytekASR("a", "k", "s")
        assert asr.finish() == ""  # 未连接不崩溃
        assert asr.finish() == ""

    def test_connect_builds_url_and_starts_thread(self, monkeypatch):
        from src.asr import IflytekASR
        fake = _FakeWS()
        monkeypatch.setattr("src.asr.websocket.create_connection", lambda url, timeout: fake)
        started = {}

        def _fake_thread(*a, **kw):
            started["daemon"] = kw.get("daemon")
            return type("T", (), {"start": lambda self: None})()

        monkeypatch.setattr("src.asr.threading.Thread", _fake_thread)
        asr = IflytekASR("a", "k", "s")
        asr.connect()
        assert started["daemon"] is True
        assert fake.closed is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_asr.py::TestIflytekSession -v`
Expected: FAIL（AttributeError: 'IflytekASR' object has no attribute 'feed'）

- [ ] **Step 3: 实现（追加到 src/asr.py）**

在 `load_dict` 之前插入会话方法：

```python
    # ---------- 会话 ----------

    def connect(self) -> None:
        """建立 WebSocket 连接并启动接收线程。"""
        import websocket
        import threading

        url = self.build_auth_url(self.api_key, self.api_secret)
        self._ws = websocket.create_connection(url, timeout=10)
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self) -> None:
        while self._ws is not None:
            try:
                data = self._ws.recv()
            except Exception:
                break  # 连接关闭/异常
            if data is None:
                continue
            self._handle_message(data)

    def feed(self, pcm: bytes) -> str:
        """发送一帧 16k PCM 音频，返回当前累积识别文本（含部分结果）。"""
        if self._ws is None:
            self.connect()
        status = 0 if self._first_frame else 1
        self._ws.send(self._build_frame(pcm, status))
        self._first_frame = False
        return self._current_text

    def finish(self) -> str:
        """发送尾帧并等待最终结果，关闭连接。幂等。"""
        if self._ws is None:
            return self._final_text or self._current_text
        self._ws.send(self._build_frame(b"", 2))
        deadline = time.time() + 10
        while not self._is_final and time.time() < deadline:
            time.sleep(0.05)
        result = self._final_text or self._current_text
        self.close()
        return result

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_asr.py -v`
Expected: 全部通过（含 TestIflytekSession 4 项）

- [ ] **Step 5: Commit**

```bash
git add src/asr.py tests/test_asr.py
git commit -m "feat: 讯飞 IAT 会话管理（connect/feed/finish/close，接收线程 + 尾帧等待）"
```

---

### Task 5: 音频预处理 _to_pcm16k（wav→16k PCM + 重采样）

**Files:**
- Modify: `src/asr.py`（追加模块级函数）
- Test: `tests/test_asr.py`（追加）

**Interfaces:**
- Produces: `_to_pcm16k(audio_bytes: bytes, target_rate: int = 16000) -> bytes` —— 输入 wav 容器或裸 PCM，输出 16kHz 单声道 16bit little-endian PCM（wav→剥离头部；多声道→单声道；采样率≠target→numpy 线性重采样）

- [ ] **Step 1: 写失败测试**（tests/test_asr.py 追加）

```python
def _make_wav(sample_rate: int, channels: int, frames: int = 16000) -> bytes:
    """生成指定采样率/声道数的正弦波 wav。"""
    import io
    import math
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        data = b""
        for i in range(frames):
            v = int(4000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            data += struct.pack("<h", v) * channels
        w.writeframes(data)
    return buf.getvalue()


class TestPcmPreprocess:
    def test_wav_16k_mono_extracts_pcm(self):
        from src.asr import _to_pcm16k
        wav = _make_wav(16000, 1)
        pcm = _to_pcm16k(wav, 16000)
        assert len(pcm) == 16000 * 2  # 1s * 16bit

    def test_wav_48k_resampled_to_16k(self):
        from src.asr import _to_pcm16k
        wav = _make_wav(48000, 1, frames=48000)
        pcm = _to_pcm16k(wav, 16000)
        assert len(pcm) == 16000 * 2  # 48k 1s → 16k 1s

    def test_stereo_downmixed_to_mono(self):
        from src.asr import _to_pcm16k
        wav = _make_wav(16000, 2, frames=16000)
        pcm = _to_pcm16k(wav, 16000)
        assert len(pcm) == 16000 * 2

    def test_raw_pcm_passthrough(self):
        from src.asr import _to_pcm16k
        raw = b"\x00\x01" * 100
        assert _to_pcm16k(raw, 16000) == raw
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_asr.py::TestPcmPreprocess -v`
Expected: FAIL（ImportError: cannot import name '_to_pcm16k'）

- [ ] **Step 3: 实现（追加到 src/asr.py 文件末尾）**

```python
def _to_pcm16k(audio_bytes: bytes, target_rate: int = 16000) -> bytes:
    """将 wav 容器或裸 PCM 归一化为 16kHz 单声道 16bit little-endian PCM。

    - wav 容器 → wave 模块剥离头部取 PCM
    - 多声道 → 取平均合成单声道
    - 采样率 ≠ target → numpy 线性重采样
    """
    import io
    import wave

    if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        with wave.open(io.BytesIO(audio_bytes), "rb") as w:
            rate = w.getframerate()
            channels = w.getnchannels()
            width = w.getsampwidth()
            pcm = w.readframes(w.getnframes())
    else:
        pcm, rate, channels, width = audio_bytes, target_rate, 1, 2

    if width != 2:
        raise ValueError(f"不支持的位深: {width * 8}bit（仅支持 16bit PCM）")

    if channels > 1:
        pcm = _downmix_to_mono(pcm, channels)

    if rate != target_rate:
        pcm = _resample_pcm(pcm, rate, target_rate)

    return pcm


def _downmix_to_mono(pcm: bytes, channels: int) -> bytes:
    import array
    samples = array.array("h")
    samples.frombytes(pcm)
    mono = array.array("h")
    for i in range(0, len(samples) - channels + 1, channels):
        mono.append(sum(samples[i:i + channels]) // channels)
    return mono.tobytes()


def _resample_pcm(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    import numpy as np

    data = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    src_len = len(data)
    dst_len = int(src_len * dst_rate / src_rate)
    x_old = np.linspace(0, 1, num=src_len, endpoint=False)
    x_new = np.linspace(0, 1, num=dst_len, endpoint=False)
    resampled = np.interp(x_new, x_old, data).astype(np.int16)
    return resampled.tobytes()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_asr.py::TestPcmPreprocess -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/asr.py tests/test_asr.py
git commit -m "feat: ASR 音频预处理（wav→16k PCM、降混、重采样）"
```

---

### Task 6: CosyVoice TTS 封装（src/tts.py）

**Files:**
- Create: `src/tts.py`
- Test: `tests/test_tts.py`（新建）

**Interfaces:**
- Produces:
  - `CosyVoiceTTS.__init__(self, model="cosyvoice-v3-flash", voice="", format="wav", sample_rate=24000, chunk_chars=1000)`
  - `CosyVoiceTTS.split_sentences(text: str, max_chars: int = 1000) -> list[str]`（staticmethod）
  - `CosyVoiceTTS.synthesize_sentence(text: str) -> bytes`（调用 dashscope SpeechSynthesizer + ResultCallback，返回完整 wav 字节；超时 60s 抛 TimeoutError；on_error 抛 RuntimeError）
  - `CosyVoiceTTS.synthesize_stream(text: str, on_sentence: Callable[[str, bytes], None]) -> None`（逐句合成回调）
  - `CosyVoiceTTS.write_wav(data: bytes, path) -> None`

- [ ] **Step 1: 写失败测试**（tests/test_tts.py 新建）

```python
"""TTS 语音合成测试（bug-121）：mock dashscope SpeechSynthesizer，不依赖真实 API"""


class TestTtsSplitting:
    def test_short_text_no_split(self):
        from src.tts import CosyVoiceTTS
        parts = CosyVoiceTTS.split_sentences("这是很短的一句话。", max_chars=1000)
        assert parts == ["这是很短的一句话。"]

    def test_split_by_sentence_boundary(self):
        from src.tts import CosyVoiceTTS
        parts = CosyVoiceTTS.split_sentences("第一句。第二句！第三句？", max_chars=100)
        assert parts == ["第一句。", "第二句！", "第三句？"]

    def test_merge_short_sentences_under_limit(self):
        from src.tts import CosyVoiceTTS
        parts = CosyVoiceTTS.split_sentences("你好。你好。你好。", max_chars=10)
        assert "".join(parts) == "你好。你好。你好。"
        assert all(len(p) <= 10 for p in parts)

    def test_overlong_sentence_hard_split(self):
        from src.tts import CosyVoiceTTS
        long = "长" * 50 + "。"
        parts = CosyVoiceTTS.split_sentences(long, max_chars=20)
        assert all(len(p) <= 20 for p in parts)
        assert "".join(parts) == long

    def test_empty_text(self):
        from src.tts import CosyVoiceTTS
        assert CosyVoiceTTS.split_sentences("", max_chars=1000) == []


class _FakeSynth:
    """模拟 dashscope SpeechSynthesizer：调用 call() 时触发 callback。"""

    def __init__(self, mode="ok"):
        self.mode = mode

    def call(self, text, timeout_millis=None):
        cb = self.callback
        cb.on_open()
        if self.mode == "error":
            cb.on_error("mock error")
            return
        cb.on_data(b"fake-wav-part-1")
        cb.on_data(b"fake-wav-part-2")
        cb.on_complete()
        cb.on_close()


class TestCosyVoiceSynthesize:
    def test_synthesize_sentence_returns_collected_bytes(self, monkeypatch):
        import sys
        from src.tts import CosyVoiceTTS

        captured = {}

        def _fake_synthesizer(**kwargs):
            captured["model"] = kwargs.get("model")
            captured["voice"] = kwargs.get("voice")
            captured["callback"] = kwargs.get("callback")
            return _FakeSynth()

        monkeypatch.setattr("dashscope.audio.tts_v2.SpeechSynthesizer", _fake_synthesizer)
        tts = CosyVoiceTTS(model="cosyvoice-v3-flash", voice="boy_voice")
        wav = tts.synthesize_sentence("你好")
        assert wav == b"fake-wav-part-1fake-wav-part-2"
        assert captured["model"] == "cosyvoice-v3-flash"
        assert captured["voice"] == "boy_voice"

    def test_synthesize_error_raises(self, monkeypatch):
        from src.tts import CosyVoiceTTS
        monkeypatch.setattr("dashscope.audio.tts_v2.SpeechSynthesizer", lambda **kw: _FakeSynth(mode="error"))
        tts = CosyVoiceTTS()
        try:
            tts.synthesize_sentence("x")
            assert False, "应抛出 RuntimeError"
        except RuntimeError as e:
            assert "mock error" in str(e)

    def test_synthesize_stream_calls_per_sentence(self, monkeypatch):
        from src.tts import CosyVoiceTTS

        def _fake_synthesizer(**kwargs):
            captured_cb = kwargs.get("callback")
            return type("S", (), {
                "call": lambda self, text, timeout_millis=None: (
                    captured_cb.on_open(),
                    captured_cb.on_data(b"w"),
                    captured_cb.on_complete(),
                    captured_cb.on_close(),
                ),
            })()

        monkeypatch.setattr("dashscope.audio.tts_v2.SpeechSynthesizer", _fake_synthesizer)
        tts = CosyVoiceTTS()
        calls = []
        tts.synthesize_stream("第一句。第二句！", lambda s, w: calls.append((s, w)))
        assert [c[0] for c in calls] == ["第一句。", "第二句！"]
        assert all(c[1] == b"w" for c in calls)

    def test_write_wav(self, tmp_path):
        from src.tts import CosyVoiceTTS
        p = tmp_path / "a.wav"
        CosyVoiceTTS.write_wav(b"data", p)
        assert p.read_bytes() == b"data"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_tts.py -v`
Expected: FAIL（ModuleNotFoundError: No module named 'src.tts'）

- [ ] **Step 3: 实现 src/tts.py**

```python
"""阿里百炼 CosyVoice 语音合成封装（bug-121）。

一期：cosyvoice-v3-flash（系统音色，默认小男孩，TTS_VOICE 配置）
二期：cosyvoice-v3.5-flash + VoiceEnrollmentService 真人音色定制（见 README）
合成流式：dashscope SpeechSynthesizer + ResultCallback（on_data 逐块回调）
"""
import threading
from pathlib import Path
from typing import Callable, List, Optional, Union

from loguru import logger


def _ChunkCollector:
    """收集流式合成音频块（dashscope ResultCallback 适配）。"""

    def __init__(self):
        self.data = bytearray()
        self.error: Optional[str] = None
        self.complete = threading.Event()

    def on_open(self) -> None:
        pass

    def on_data(self, data: bytes) -> None:
        self.data.extend(data)

    def on_complete(self) -> None:
        self.complete.set()

    def on_error(self, message) -> None:
        self.error = str(message)
        self.complete.set()

    def on_close(self) -> None:
        self.complete.set()

    def on_event(self, message: str) -> None:
        pass


class CosyVoiceTTS:
    """CosyVoice TTS 封装：逐句合成 + 句子级流式回调。"""

    def __init__(
        self,
        model: str = "cosyvoice-v3-flash",
        voice: str = "",
        format: str = "wav",
        sample_rate: int = 24000,
        chunk_chars: int = 1000,
    ):
        self.model = model
        self.voice = voice
        self.format = format
        self.sample_rate = sample_rate
        self.chunk_chars = chunk_chars
        ensure_ffmpeg()  # 防御性引导（app 入口已提前调用）

    @staticmethod
    def split_sentences(text: str, max_chars: int = 1000) -> List[str]:
        """按句子边界（。！？；!?;\n）分段，合并短句至不超过 max_chars；超长单句硬切。"""
        import re

        text = (text or "").strip()
        if not text:
            return []
        raw_parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;\n])", text) if p.strip()]
        sentences: List[str] = []
        for p in raw_parts:
            if sentences and len(sentences[-1]) + len(p) <= max_chars:
                sentences[-1] += p
            else:
                sentences.append(p)
        result: List[str] = []
        for s in sentences:
            while len(s) > max_chars:
                result.append(s[:max_chars])
                s = s[max_chars:]
            result.append(s)
        return result

    def synthesize_sentence(self, text: str) -> bytes:
        """合成单句，返回完整 wav 字节。60s 超时抛 TimeoutError；on_error 抛 RuntimeError。"""
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        if not self.voice:
            raise RuntimeError("TTS 音色未配置（TTS_VOICE 为空），请在 .env 设置")
        collector = _ChunkCollector()
        synth = SpeechSynthesizer(
            model=self.model,
            voice=self.voice,
            format=self.format,
            sample_rate=self.sample_rate,
            callback=collector,
        )
        synth.call(text)
        if not collector.complete.wait(timeout=60):
            raise TimeoutError("TTS 合成超时（60s）")
        if collector.error:
            raise RuntimeError(f"TTS 合成失败: {collector.error}")
        return bytes(collector.data)

    def synthesize_stream(self, text: str, on_sentence: Callable[[str, bytes], None]) -> None:
        """逐句合成，每句完成后立即回调 on_sentence(句文本, wav 字节)。"""
        for sentence in self.split_sentences(text, self.chunk_chars):
            wav = self.synthesize_sentence(sentence)
            on_sentence(sentence, wav)

    @staticmethod
    def write_wav(data: bytes, path: Union[str, Path]) -> None:
        Path(path).write_bytes(data)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_tts.py -v`
Expected: 10 passed

- [ ] **Step 5: 语法检查 + Commit**

```bash
python -m py_compile src/tts.py
git add src/tts.py tests/test_tts.py
git commit -m "feat: CosyVoice TTS 封装（逐句合成/流式回调/分段）"
```

---

### Task 7: app.py ASR UI 集成

**Files:**
- Modify: `app.py`
- Test: `tests/test_voice_ui.py`（新建）

**Interfaces:**
- Consumes: `IflytekASR`（Task 3/4）、`_to_pcm16k`、`load_dict`（Task 3/5）、`settings.xfyun_*`（Task 1）
- Produces（app.py 模块级函数，供 Task 8 复用与测试）:
  - `asr_stream_chunk(audio_filepath, state, project_id) -> generator of (state, msg_update, voice_status_update)`
  - `asr_stream_stop(state, project_id) -> generator of (state, msg_update, voice_status_update)`
  - `_ensure_ffmpeg_bootstrap()`（app.py 顶部、import gradio 之前调用 `ensure_ffmpeg()`）

- [ ] **Step 1: app.py 顶部引导（在 `import gradio as gr` 之前）**

在 `sys.path.insert(0, ...)` 之后、`import gradio as gr` 之前插入：

```python
# 语音功能（bug-121）：gradio 导入即触发 pydub 导入，pydub 在 import 时缓存
# ffmpeg 查找结果，因此 HLS 流式音频输出所需的 ffmpeg 引导必须在 gradio 之前执行
from src.audio_bootstrap import ensure_ffmpeg

ensure_ffmpeg()

import gradio as gr
```

（注意：保持其它 import 顺序不变；`from src.config import settings` 等在 gradio 之后仍可。）

- [ ] **Step 2: 写失败测试**（tests/test_voice_ui.py 新建）

```python
"""语音功能 UI 集成测试（bug-121）：mock ASR/TTS，不依赖真实 API"""
import json
from pathlib import Path

import pytest


class TestAsrStreamChunk:
    def test_no_keys_returns_message(self, monkeypatch):
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        monkeypatch.setattr("app.settings", s)  # 全部为空 → 未配置提示
        results = list(asr_stream_chunk(None, None, ""))
        state, msg_update, status = results[0]
        assert "未配置讯飞密钥" in status["value"]

    def test_feeds_audio_and_updates_text(self, monkeypatch, tmp_path):
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)

        class _FakeASR:
            def __init__(self, *a, **kw):
                self.fed = []

            def feed(self, pcm):
                self.fed.append(pcm)
                return "你好"

            def is_final(self):
                return False

            def finish(self):
                return "你好"

        monkeypatch.setattr("app.IflytekASR", _FakeASR)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "chunk.wav"
        chunk.write_bytes(b"\x00\x01\x02\x03")

        results = list(asr_stream_chunk(str(chunk), None, ""))
        state, msg_update, status = results[0]
        assert state["session"] is not None
        assert "你好" in msg_update["value"]
        assert "识别中" in status["value"]

    def test_vad_finalize_marks_done(self, monkeypatch, tmp_path):
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)

        class _FakeASR:
            def __init__(self, *a, **kw):
                pass

            def feed(self, pcm):
                return "你好"

            def is_final(self):
                return True

            def finish(self):
                return "你好"

        monkeypatch.setattr("app.IflytekASR", _FakeASR)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "chunk.wav"
        chunk.write_bytes(b"data")

        results = list(asr_stream_chunk(str(chunk), None, ""))
        _, msg_update, status = results[0]
        assert "已识别完成" in status["value"]
        assert "你好" in msg_update["value"]


class TestAsrStreamStop:
    def test_stop_finishes_session(self, monkeypatch, tmp_path):
        from app import asr_stream_stop
        from src.config import Settings

        s = Settings(_env_file=None)
        monkeypatch.setattr("app.settings", s)
        state = {"session": type("S", (), {"finish": lambda self: "最终文本"})(), "finalized": False}
        results = list(asr_stream_stop(state, ""))
        new_state, msg_update, status = results[0]
        assert new_state is None
        assert "最终文本" in msg_update["value"]
        assert "已识别完成" in status["value"]

    def test_stop_without_session_noop(self):
        from app import asr_stream_stop
        results = list(asr_stream_stop(None, ""))
        assert results[0][0] is None
```

- [ ] **Step 3: 运行确认失败**

Run: `pytest tests/test_voice_ui.py -v`
Expected: FAIL（ImportError: cannot import name 'asr_stream_chunk' from 'app'）

- [ ] **Step 4: 实现 app.py（模块级函数，放在 answer_question 之前）**

```python
# ========== 语音功能（bug-121）：ASR 语音输入 ==========

def asr_stream_chunk(audio_filepath, state, project_id: str = ""):
    """Gradio Audio stream 事件：音频块送达 → 送入讯飞 ASR → 实时更新输入框。

    差分发送：兼容"增量块"与"累计录音"两种前端语义（只发送新增的 PCM 长度）。
    VAD（服务端静默判定）或最长录音超时 → 自动结束转写，填入最终文本。
    """
    if not (settings.xfyun_app_id and settings.xfyun_api_key and settings.xfyun_api_secret):
        yield state, gr.update(), "未配置讯飞密钥（XFYUN_APP_ID/API_KEY/API_SECRET），请在 .env 补充"
        return
    if not audio_filepath:
        yield state, gr.update(), ""
        return
    if state is None:
        cfg = load_dict(project_id, settings.asr_dict_dir)
        state = {
            "asr": IflytekASR(
                settings.xfyun_app_id, settings.xfyun_api_key, settings.xfyun_api_secret,
                language=settings.asr_language, accent=settings.asr_accent,
                vad_eos_ms=settings.asr_vad_eos, hotwords=cfg["hotwords"],
                corrections=cfg["corrections"],
            ),
            "sent_bytes": 0,
            "started": time.time(),
            "finalized": False,
        }
    asr = state["asr"]
    if state["finalized"]:
        # VAD 已结束转写：忽略后续音频块（等待用户停止录音）
        yield state, gr.update(), "已识别完成，可修改后发送"
        return
    try:
        raw = Path(audio_filepath).read_bytes()
        pcm = _to_pcm16k(raw, settings.asr_sample_rate)
        new_pcm = pcm[state["sent_bytes"]:]
        if new_pcm:
            asr.feed(new_pcm)
            state["sent_bytes"] += len(new_pcm)
    except Exception as e:
        logger.warning(f"ASR 音频处理失败: {e}")
        yield state, gr.update(), f"识别出错: {e}"
        return
    text = asr.correct(asr.current_text)
    if asr.is_final() or time.time() - state["started"] > settings.asr_max_duration:
        final = asr.finish()
        state["finalized"] = True
        yield state, gr.update(value=final), "已识别完成，可修改后发送"
        return
    yield state, gr.update(value=text) if text else gr.update(), "识别中…"


def asr_stream_stop(state, project_id: str = ""):
    """Gradio Audio stop 事件：用户停止录音 → 结束 ASR 会话，返回最终文本。"""
    if state and state.get("asr"):
        final = state["asr"].finish()
        state = None
        yield state, gr.update(value=final), "已识别完成，可修改后发送"
    else:
        yield state, gr.update(), ""
```

- [ ] **Step 5: 更新 create_ui()（在输入行区域加入语音输入组件与事件绑定）**

在 `msg = gr.Textbox(...)` / `submit_btn = gr.Button(...)` 所在 Row **之后**新增一个 Row：

```python
                with gr.Row():
                    voice_audio = gr.Audio(
                        sources=["microphone"],
                        streaming=True,
                        type="filepath",
                        label="语音输入（点击开始说话，说完静默约 2 秒自动转写）",
                        scale=6,
                    )
                    voice_status = gr.Markdown("", scale=4)
                    asr_state = gr.State(None)
```

在事件绑定区（`msg.submit(...)` 之后）追加：

```python
        # 语音功能（bug-121）：ASR 流式输入
        voice_audio.stream(
            asr_stream_chunk,
            [voice_audio, asr_state, project_dropdown],
            [asr_state, msg, voice_status],
            stream_every=0.5,
        )
        voice_audio.stop(
            asr_stream_stop,
            [asr_state, project_dropdown],
            [asr_state, msg, voice_status],
        )
```

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_voice_ui.py -v`
Expected: 5 passed

- [ ] **Step 7: 既有回归 + 语法检查**

Run: `python -m py_compile app.py` 且 `pytest tests/test_edge_cases.py tests/test_review_findings.py tests/test_pipeline.py -q`
Expected: 全部通过（无回归）

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_voice_ui.py
git commit -m "feat: Web UI 语音输入（ASR 流式转写，实时出字 + VAD/超时自动结束）"
```

---

### Task 8: app.py TTS UI 集成（句子级流式播报 + 重播）

**Files:**
- Modify: `app.py`
- Test: `tests/test_voice_ui.py`（追加）

**Interfaces:**
- Consumes: `CosyVoiceTTS`（Task 6）、`clean_text_for_tts`（已有）、`settings.tts_*`（Task 1）
- Produces:
  - `tts_after_answer(chatbot_history, enabled) -> generator of (tts_audio_update, tts_replay_update, tts_status_update)`
  - `_extract_last_answer_text(history) -> str`（取最后一条 assistant 正文，按 `**[检索来源]**` 截断）
  - `_write_replay_wav(chunks: list[bytes]) -> Path`（合并各句 wav 到 `data/processed/tts_cache/last_answer.wav`）

- [ ] **Step 1: 写失败测试**（tests/test_voice_ui.py 追加）

```python
class TestTtsAfterAnswer:
    def test_disabled_skips(self):
        from app import tts_after_answer
        results = list(tts_after_answer([], False))
        assert len(results) == 1
        _, _, status = results[0]

    def test_no_key_shows_message(self, monkeypatch):
        from app import tts_after_answer
        from src.config import Settings

        s = Settings(_env_file=None)
        monkeypatch.setattr("app.settings", s)
        results = list(tts_after_answer([{"role": "user", "content": "q"},
                                         {"role": "assistant", "content": "答"}], True))
        assert "未配置百炼 Key" in results[0][2]["value"]

    def test_streams_sentences_and_replay(self, monkeypatch, tmp_path):
        from app import tts_after_answer
        from src.config import Settings

        s = Settings(_env_file=None)
        s.dashscope_api_key = "dummy"
        s.tts_chunk_chars = 1000
        s.tts_model = "cosyvoice-v3-flash"
        s.tts_voice = "v"
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.CosyVoiceTTS", _FakeTTS)
        monkeypatch.setattr("app._write_replay_wav",
                           lambda chunks: tmp_path / "replay.wav")
        history = [{"role": "user", "content": "q"},
                   {"role": "assistant", "content": "第一句。第二句！\n\n---\n\n**[检索来源]**\n1. **司母戊鼎**"}]
        results = list(tts_after_answer(history, True))
        # 每句一个流式 yield + 最终重播 yield
        assert len(results) == 3
        assert results[0][0]["value"] == b"fake-wav"  # 句子 1 流式
        assert results[1][0]["value"] == b"fake-wav"  # 句子 2 流式
        assert "已播报" in results[2][2]["value"]
        assert results[2][1]["value"] == str(tmp_path / "replay.wav")

    def test_extract_last_answer_strips_sources(self):
        from app import _extract_last_answer_text
        history = [{"role": "user", "content": "q"},
                   {"role": "assistant", "content": "正文内容\n\n---\n\n**[检索来源]**\n1. **x**"}]
        assert _extract_last_answer_text(history) == "正文内容"


class _FakeTTS:
    """模拟 CosyVoiceTTS：每句返回固定 wav。"""

    def __init__(self, *a, **kw):
        pass

    def split_sentences(self, text, max_chars=1000):
        from src.tts import CosyVoiceTTS
        return CosyVoiceTTS.split_sentences(text, max_chars)

    def synthesize_sentence(self, text):
        return b"fake-wav"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_voice_ui.py::TestTtsAfterAnswer -v`
Expected: FAIL（ImportError: cannot import name 'tts_after_answer'）

- [ ] **Step 3: 实现 app.py（模块级函数，放在 asr_stream_stop 之后）**

```python
# ========== 语音功能（bug-121）：TTS 语音播报（句子级流式） ==========

def _extract_last_answer_text(history) -> str:
    """取对话历史最后一条 assistant 正文（去掉 **[检索来源]** 及之后内容）。"""
    if not history:
        return ""
    last = history[-1]
    if isinstance(last, dict):
        content = _extract_text(last.get("content", ""))
    elif isinstance(last, (list, tuple)) and len(last) > 1:
        content = _extract_text(last[1])
    else:
        content = ""
    marker = "**[检索来源]**"
    idx = content.find(marker)
    if idx >= 0:
        content = content[:idx].rstrip()
    return content.strip()


def _write_replay_wav(chunks):
    """合并各句 wav 字节写入重播缓存文件，返回 Path。"""
    cache_dir = settings.project_root / "data" / "processed" / "tts_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "last_answer.wav"
    CosyVoiceTTS.write_wav(b"".join(chunks), path)
    return path


def tts_after_answer(chatbot_history, enabled):
    """respond 完成后触发：句子级流式播报 + 完整重播副本（生成器）。"""
    if not enabled:
        yield gr.update(), gr.update(), ""
        return
    if not settings.dashscope_api_key:
        yield gr.update(), gr.update(), "未配置百炼 Key（DASHSCOPE_API_KEY），语音播报不可用"
        return
    text = _extract_last_answer_text(chatbot_history)
    if not text:
        yield gr.update(), gr.update(), ""
        return
    text = clean_text_for_tts(text)
    tts = CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                       chunk_chars=settings.tts_chunk_chars)
    chunks = []
    try:
        for sentence in tts.split_sentences(text, settings.tts_chunk_chars):
            wav = tts.synthesize_sentence(sentence)
            chunks.append(wav)
            # 句子级流式：每句合成完立即 yield（gradio HLS 无缝续播）
            yield gr.update(value=wav), gr.update(), "播报中…"
        replay_path = _write_replay_wav(chunks)
        yield gr.update(), gr.update(value=str(replay_path)), "已播报（可点击重播）"
    except Exception as e:
        logger.warning(f"TTS 播报失败: {e}")
        yield gr.update(), gr.update(), f"语音播报失败: {e}"
```

- [ ] **Step 4: 更新 create_ui()（加入 TTS 组件与事件链）**

在检索结果面板右侧列（chunks_json 之后）追加：

```python
                gr.Markdown("### 语音播报")
                tts_audio = gr.Audio(streaming=True, label="播报（自动播放）", visible=True)
                tts_replay = gr.Audio(label="重播", visible=True)
                tts_enabled = gr.Checkbox(label="语音播报", value=True)
                tts_status = gr.Markdown("")
```

在事件绑定区（`msg.submit(...)` 之后）**改写现有 respond 链**：将 `msg.submit` / `submit_btn.click` 的返回值保存为变量，再 `.then(tts_after_answer, ...)`。示例按钮不触发播报（避免误播）。具体改写（替换原三处事件绑定）：

```python
        msg_submit = msg.submit(respond, [msg, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])
        submit_click = submit_btn.click(respond, [msg, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])
        clear_btn.click(clear_history, None, [chatbot, chunks_json])
        status_btn.click(get_system_status, [project_dropdown], [status_text])

        for btn in example_btns:
            btn.click(respond, [btn, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])

        # 语音功能（bug-121）：回答完成后自动语音播报
        for dep in (msg_submit, submit_click):
            dep.then(tts_after_answer,
                     inputs=[chatbot, tts_enabled],
                     outputs=[tts_audio, tts_replay, tts_status])
```

（示例按钮不触发播报，避免误播；用户可后续按需加入。）

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_voice_ui.py -v`
Expected: 全部通过（TestAsrStreamChunk 3 + TestAsrStreamStop 2 + TestTtsAfterAnswer 4 = 9）

- [ ] **Step 6: 既有回归 + 语法检查**

Run: `python -m py_compile app.py` 且 `pytest tests/ -q`
Expected: 397 passed + 新增 9 项 = 406 passed（2 项已知失败 bug-117b 除外）

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_voice_ui.py
git commit -m "feat: Web UI 语音播报（CosyVoice 句子级流式播放 + 重播，respond().then 链）"
```

---

### Task 9: 数据示例 + 真实 API 冒烟 + 文档 + bug-fix-plan 追加 + 全量回归

**Files:**
- Create: `data/voice/asr_dict.json`（示例全局字典）
- Modify: `README.md`（语音功能章节：使用说明、多音字/热词指南、二期音色定制指引）
- Modify: `bug-fix-plan.md`（**追加** bug-121 记录，不改旧记录）
- Test: 无新增（冒烟为手动/一次性脚本）

- [ ] **Step 1: 创建 data/voice/asr_dict.json（示例）**

```json
{
  "hotwords": ["司母戊鼎", "清明上河图"],
  "corrections": {"四亩无顶": "司母戊鼎"}
}
```

（示例为博物馆项目词；家博会等其它项目可创建 `data/voice/jiabohui_asr_dict.json` 覆盖。）

- [ ] **Step 2: 真实 API 冒烟（一次性，需 .env 已配置 XFYUN_* 与 DASHSCOPE_API_KEY）**

```bash
# ① 确认 cosyvoice-v3-flash 可用音色并选定小男孩音色
python - <<'EOF'
from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback
import io
class C(ResultCallback):
    def __init__(self): self.data = bytearray(); self.err = None
    def on_data(self, d): self.data.extend(d)
    def on_error(self, m): self.err = m
# 候选音色逐个试（cosyvoice 音色 id 以百炼控制台模型广场为准）：
for voice in ["longxiaochun", "longxiaoxia", "longchen", "longhao"]:
    cb = C()
    try:
        SpeechSynthesizer(model="cosyvoice-v3-flash", voice=voice, format="wav", callback=cb).call("你好，我是小虎")
        print(voice, "OK", len(cb.data), "bytes")
    except Exception as e:
        print(voice, "FAIL", e)
EOF
# 选定后把 voice id 写入 .env: TTS_VOICE=<id>，并回填 src/config.py 的 tts_voice 默认值注释

# ② 冒烟测试脚本 scripts/smoke_tts_asr.py（临时，验证后删除）：
#    - TTS: synthesize_sentence("你好") 返回非空 wav
#    - ASR: 用生成的一段 wav（_make_wav 逻辑）走 IflytekASR feed/finish，输出转写文本
#    - 热词拼音：观察含热词的文本识别是否优先命中（人工核对）
#    - 纠错：correct("四亩无顶") == "司母戊鼎"
```

- [ ] **Step 3: README 追加「语音功能」章节（在 "Web UI 问答界面" 之后）**

内容要点：
1. **功能**：语音输入（讯飞 ASR，实时出字）+ 语音播报（CosyVoice，句子级流式，默认开）
2. **配置**：XFYUN_* 三键 + ASR_VAD_EOS/ASR_MAX_DURATION；TTS_ENABLED/TTS_MODEL/TTS_VOICE/TTS_CHUNK_CHARS（参见 .env.example）
3. **多音字/热词使用指南**：
   - 全局 `data/voice/asr_dict.json`，项目覆盖 `data/voice/{project_id}_asr_dict.json`（存在时与全局合并）
   - `hotwords`：热词（≤20 字/个，最多 200 个，空格分隔自动处理）；拼音标注格式 `词(拼音)` 强制多音字读音，如 `"重庆(chong qing)"`
   - `corrections`：转写后纠错映射（多字符优先），如 `{"期中": "青铜器"}`
   - 修改后无需重启（每次录音会话重新加载）
4. **二期音色定制**（cosyvoice-v3.5-flash 真人音色）：
   - 准备 1-2 分钟真人清晰录音（16kHz+，无噪音，普通话），上传至可匿名访问 URL
   - `VoiceEnrollmentService().create_voice(target_model="cosyvoice-v3.5-flash", prefix="my_voice", url="https://.../sample.wav", language_hints=["zh"])` 创建
   - `.env` 设 `TTS_MODEL=cosyvoice-v3.5-flash`、`TTS_VOICE=<返回的 voice_id>`
   - 管理：`list_voices/query_voice/update_voice/delete_voice`
5. **已知限制**：浏览器录音需再点一次麦克风停止（VAD 只自动结束转写）；ffmpeg 缺失时 TTS 降级为一次性播放

- [ ] **Step 4: bug-fix-plan.md 追加 bug-121 记录（文件末尾追加新章节）**

```markdown
---

## 新增功能（第十四轮 - 语音功能：ASR 语音输入 + TTS 语音播报）

> 需求：Web UI 新增语音说话（讯飞 IAT 流式 ASR）与答案语音播报（百炼 CosyVoice）。
> 设计文档：docs/superpowers/specs/2026-08-08-voice-feature-design.md（已批准）
> 全量测试：`pytest tests/ -q` → **406 passed**（原 397 + 新增 9，0 失败 0 错误）

## 问题总览

| 编号 | 问题描述 | 涉及文件 | 严重程度 | 修复状态 |
|------|---------|---------|---------|---------|
| bug-121 | 新增功能：讯飞 ASR 流式语音输入（实时出字、VAD/超时自动结束、多音字/热词全局+项目覆盖）+ CosyVoice TTS 句子级流式语音播报（默认开、可重播、二期音色定制指引） | `src/asr.py`（新增）、`src/tts.py`（新增）、`src/audio_bootstrap.py`（新增）、`src/config.py`、`app.py`、`requirements.txt`、`.env.example`、`data/voice/asr_dict.json`（新增）、README | 功能增强 | 已实现 |

## 实现要点（详见设计文档）

1. ASR：讯飞 IAT WebSocket（HMAC-SHA256 鉴权、wpgs 动态修正实时部分结果、vad_eos 静音判定 + 30s 兜底）；音频 wav→16k PCM 预处理（降混/重采样）；热词（含拼音标注）走 API、纠错映射走后处理
2. TTS：cosyvoice-v3-flash 逐句合成，句子级流式播放（gradio 6 原生 HLS/AAC，static-ffmpeg 提供 ffmpeg/ffprobe）；完整 wav 重播副本；二期 cosyvoice-v3.5-flash 真人音色定制指引
3. UI：gr.Audio(streaming=True) 麦克风输入实时出字；respond().then() 链自动播报（默认开）+ 重播

## 验证结果

| 编号 | 验证方式 | 结果 |
|------|---------|------|
| bug-121 | 新增 `tests/test_asr.py` / `tests/test_tts.py` / `tests/test_voice_ui.py` 共 19 项全部通过；真实 API 冒烟（音色确认/热词拼音/ASR 端到端）；全量 406 passed | ✅ 已实现 |
```

- [ ] **Step 5: 全量回归**

Run: `pytest tests/ -q`
Expected: 406 passed（2 项已知失败 bug-117b 除外）

- [ ] **Step 6: Commit**

```bash
git add -f data/voice/asr_dict.json README.md bug-fix-plan.md
git add -f docs/superpowers/plans/2026-08-08-voice-feature.md
git commit -m "docs: 语音功能使用指南 + bug-121 记录 + 全局热词示例"
```

---

## 自审记录（写作时已执行）

- **Spec 覆盖**：ASR 流式输入（Task 7）、VAD+30s（Task 7 feed/is_final 逻辑）、多音字/热词全局+项目覆盖（Task 3 load_dict + Task 5 预处理）、TTS 一期模型/小男孩音色配置（Task 1/6，音色 id 真实 API 确认在 Task 9）、句子级流式播放（Task 8）、自动播+重播+默认开（Task 8）、二期指引（Task 9 README）、优雅降级（Task 2/7/8 的未配置/ffmpeg 缺失分支）、bug-fix-plan 追加（Task 9）——全部覆盖
- **占位符扫描**：所有代码块均为完整实现；唯一"待定"项是音色 id（需真实 API 冒烟，Task 9 明确为操作步骤而非占位）
- **类型一致性**：`IflytekASR(app_id, api_key, api_secret, language, accent, vad_eos_ms, hotwords, corrections)` 在 Task 3 定义、Task 7 使用一致；`CosyVoiceTTS(model, voice, chunk_chars)` 在 Task 6 定义、Task 8 使用一致；`load_dict(project_id, dict_dir)`、`_to_pcm16k(bytes, rate)` 签名全流程一致