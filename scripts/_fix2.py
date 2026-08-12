"""一次性：修控制字节 + TestPlayGreeting 补 pending 门闩前提。"""

p = "tests/test_voice_assist.py"
data = open(p, "rb").read()
data = data.replace(b"\x00", b"\\x00").replace(b"\x01", b"\\x01")
content = data.decode("utf-8")

old1 = '''        monkeypatch.setattr(app_mod, "_greeting_pcm",
                            lambda project: b"\\x01\\x00" * 24000)  # 1s 假 PCM
        results = list(app_mod.play_greeting("#1", "museum", True, [], None))'''
new1 = '''        monkeypatch.setattr(app_mod, "_greeting_pcm",
                            lambda project: b"\\x01\\x00" * 24000)  # 1s 假 PCM
        app_mod._pending_greet.add("anon")  # 门闩前提（修复轮2b）
        results = list(app_mod.play_greeting("#1", "museum", True, [], None))'''
assert old1 in content, "old1 not found"
content = content.replace(old1, new1)

old2 = '        results = list(app_mod.play_greeting("#1", "museum", False, [], None))'
new2 = '''        app_mod._pending_greet.add("anon")
        results = list(app_mod.play_greeting("#1", "museum", False, [], None))'''
assert old2 in content, "old2 not found"
content = content.replace(old2, new2)

open(p, "w", encoding="utf-8").write(content)
print("fixed + patched")
