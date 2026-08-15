"""手写 OCR（web-003）：百炼 qwen-vl-ocr（DASHSCOPE_API_KEY 同百炼 LLM 密钥，仅服务端）。

前端手写板停笔 2s 把画布 PNG base64 POST 到 /api/ocr，本客户端负责调百炼并回文本。
dashscope 延迟 import：测试用 sys.modules 假模块替换，全程离线。
"""
from __future__ import annotations

import base64
import binascii
import logging

from src.config import settings

logger = logging.getLogger(__name__)


class OcrError(RuntimeError):
    """OCR 调用失败（含入参非法与上游错误）。"""


_OCR_PROMPT = "请识别图片中的手写文字，只输出识别到的文字本身，不要输出任何解释。"


class OcrClient:
    def __init__(self, model: str, max_image_bytes: int = 8 * 1024 * 1024):
        self.model = model
        self.max_image_bytes = max_image_bytes

    @staticmethod
    def _strip_data_url(b64: str) -> str:
        if b64.startswith("data:"):
            _, _, b64 = b64.partition(",")
        return b64.strip()

    def recognize(self, image_base64: str) -> str:
        b64 = self._strip_data_url(image_base64 or "")
        if not b64:
            raise OcrError("空图像")
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise OcrError("图像 base64 非法") from e
        if len(raw) > self.max_image_bytes:
            raise OcrError("图像过大")
        import dashscope  # 延迟导入：离线测试以假模块替换

        dashscope.api_key = settings.dashscope_api_key
        resp = dashscope.MultiModalConversation.call(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"image": f"data:image/png;base64,{b64}"},
                {"text": _OCR_PROMPT},
            ]}],
        )
        if getattr(resp, "status_code", 500) != 200:
            logger.warning("OCR 上游错误: %s %s", getattr(resp, "code", ""), getattr(resp, "message", ""))
            raise OcrError("OCR 服务暂不可用")
        content = resp.output.choices[0].message.content
        if isinstance(content, list):
            text = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        else:
            text = str(content or "")
        return text.strip()
