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