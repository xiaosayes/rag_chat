"""讯飞语音听写（IAT）WebSocket 客户端（bug-121）。

协议：wss://iat-api.xfyun.cn/v2/iat
  - 鉴权：HMAC-SHA256 签名（host/date/request-line）→ base64 authorization
  - 请求帧：common(app_id) + business(language/domain/accent/vad_eos/dwa) + data(status/format/encoding/audio)
  - 响应：code=0 时 data.result 含部分结果（wpgs 动态修正：pgs=rpl 替换 / apd 追加；ls=true 为最终结果）
"""
import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
from email.utils import formatdate
from pathlib import Path
from typing import Dict, List, Optional

import websocket
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
        # 注：讯飞 IAT v2 真实接口不支持 business.hotwords
        # （实测 10163: param validate error: '$.business.hotwords' unknown field），
        # 热词需走 vocabulary_id 热词表接口（二期）。配置保留在 asr_dict.json，此处不再发送。
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


    # ---------- 会话 ----------

    def connect(self) -> None:
        """建立 WebSocket 连接并启动接收线程。"""
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
