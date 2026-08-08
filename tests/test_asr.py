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
