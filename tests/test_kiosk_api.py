# web-001：kiosk_server 独立配置（os.getenv 直读，不改 src/config.py）
import base64
import os
import sys
import types

import pytest

from kiosk_server.config import KioskConfig

# web-003：手写 OCR（百炼 qwen-vl-ocr，密钥仅服务端，dashscope 全 mock）
from kiosk_server.ocr import OcrClient, OcrError

# web-002：预设问题池（服务器 JSON 全量池 + 缺省兜底；前端随机抽 8 展示）
from kiosk_server.presets import DEFAULT_PRESETS, load_presets


class TestKioskConfig:
    def test_defaults(self, monkeypatch):
        for k in list(os.environ):
            if k.startswith("KIOSK_"):
                monkeypatch.delenv(k)
        cfg = KioskConfig.from_env()
        assert cfg.port == 7861
        assert cfg.token == ""                 # 默认不鉴权（内网）
        assert cfg.persona == "湘小图"
        assert cfg.ocr_model == "qwen-vl-ocr-latest"
        assert cfg.project_id == "jiabohui"
        assert cfg.idle_home_s == 150.0
        assert cfg.idle_refresh_s == 300.0
        assert cfg.cors_origins == ("*",)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KIOSK_API_PORT", "9000")
        monkeypatch.setenv("KIOSK_API_TOKEN", "s3cret")
        monkeypatch.setenv("KIOSK_CORS_ORIGINS", "http://a.local, http://b.local ,")
        monkeypatch.setenv("KIOSK_PRESETS_PATH", "data/kiosk/x.json")
        cfg = KioskConfig.from_env()
        assert cfg.port == 9000
        assert cfg.token == "s3cret"
        assert cfg.cors_origins == ("http://a.local", "http://b.local")
        assert cfg.presets_path == "data/kiosk/x.json"


class TestPresets:
    def test_default_pool(self):
        assert len(DEFAULT_PRESETS) == 16
        assert len(set(DEFAULT_PRESETS)) == 16          # 无重复
        assert all(isinstance(q, str) and q.strip() for q in DEFAULT_PRESETS)

    def test_missing_file_falls_back(self, tmp_path):
        assert load_presets(str(tmp_path / "none.json")) == DEFAULT_PRESETS

    def test_valid_file(self, tmp_path):
        p = tmp_path / "p.json"
        p.write_text('{"questions": ["问题甲", " ", "问题乙", "问题甲", ""]}', encoding="utf-8")
        assert load_presets(str(p)) == ["问题甲", "问题乙"]   # 去空去重保序

    def test_bare_list_file(self, tmp_path):
        p = tmp_path / "p.json"
        p.write_text('["q1", "q2"]', encoding="utf-8")
        assert load_presets(str(p)) == ["q1", "q2"]

    def test_invalid_json_falls_back(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_presets(str(p)) == DEFAULT_PRESETS

    def test_empty_list_falls_back(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text('{"questions": []}', encoding="utf-8")
        assert load_presets(str(p)) == DEFAULT_PRESETS


def _fake_dashscope(monkeypatch, resp):
    fake = types.ModuleType("dashscope")
    captured = {}

    class _MMC:
        @staticmethod
        def call(**kwargs):
            captured.update(kwargs)
            return resp

    fake.MultiModalConversation = _MMC
    fake.api_key = None
    monkeypatch.setitem(sys.modules, "dashscope", fake)
    return captured


def _ok_resp(text_parts):
    msg = types.SimpleNamespace(content=[{"text": t} for t in text_parts])
    choice = types.SimpleNamespace(message=msg)
    return types.SimpleNamespace(status_code=200, output=types.SimpleNamespace(choices=[choice]))


class TestOcrClient:
    def test_success_concatenates_parts(self, monkeypatch):
        captured = _fake_dashscope(monkeypatch, _ok_resp(["你", "好"]))
        client = OcrClient(model="m-test")
        png_b64 = base64.b64encode(b"\x89PNG fake").decode()
        assert client.recognize(f"data:image/png;base64,{png_b64}") == "你好"
        assert captured["model"] == "m-test"
        content = captured["messages"][0]["content"]
        assert content[0]["image"].startswith("data:image/png;base64,")

    def test_string_content_shape(self, monkeypatch):
        msg = types.SimpleNamespace(content="直接字符串")
        resp = types.SimpleNamespace(
            status_code=200,
            output=types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)]),
        )
        _fake_dashscope(monkeypatch, resp)
        assert OcrClient(model="m").recognize(base64.b64encode(b"x").decode()) == "直接字符串"

    def test_api_error_raises(self, monkeypatch):
        resp = types.SimpleNamespace(status_code=400, code="BadRequest", message="invalid")
        _fake_dashscope(monkeypatch, resp)
        with pytest.raises(OcrError):
            OcrClient(model="m").recognize(base64.b64encode(b"x").decode())

    def test_empty_and_invalid_and_oversize(self, monkeypatch):
        _fake_dashscope(monkeypatch, _ok_resp(["x"]))
        client = OcrClient(model="m", max_image_bytes=4)
        with pytest.raises(OcrError):
            client.recognize("")
        with pytest.raises(OcrError):
            client.recognize("!!!not-base64!!!")
        with pytest.raises(OcrError):
            client.recognize(base64.b64encode(b"12345678").decode())
