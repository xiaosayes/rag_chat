"""WS /ws/voice（web-008）：语音+问答+播报单通道全双工。

M2 子集：hello/ask/barge_in/ping + 文本与 PCM 音频下行；binary 上行（麦克风 PCM）M3 接入。
桥接：BroadcastSession.ask 在工作线程阻塞执行，事件经 asyncio.Queue 单点发送
（emit 可被任意线程调用 → loop.call_soon_threadsafe 转入事件循环）。
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from src.config import settings

from . import __version__, services
from .config import KioskConfig
from .voice import VoiceSession

logger = logging.getLogger(__name__)


def _default_session_factory(cfg: KioskConfig):
    def make(emit) -> VoiceSession:
        # web-036：联网搜索兜底包装（知识库拒答→百炼联网作答；开关关闭=原内核行为）
        from .web_fallback import WebFallbackPipeline
        pipe = WebFallbackPipeline(services.get_pipeline(cfg.project_id),
                                   enabled=cfg.web_fallback)

        def tts_factory():
            try:
                return services.make_tts()
            except Exception as e:
                logger.warning("TTS 初始化失败（本轮纯文本）: %s", e)
                return None

        assistant = services.make_voice_assistant(cfg.project_id)   # None=降级纯手动
        vs = VoiceSession(
            pipe, tts_factory, assistant, emit,
            greeting_pcm_fn=lambda: services.greeting_pcm(cfg.project_id),
            accum_chars=settings.tts_accum_chars,
            watchdog_s=settings.tts_stream_watchdog_seconds,
            first_floor_chars=cfg.first_floor_chars,   # web-030 首播硬地板
        )
        if cfg.story_enabled:                        # web-058：绘本编排全套真件注入
            from .story import (ImageClient, ScriptClient, StoryCache,
                                StorySession, classify_intent_llm)

            def story_factory(story_emit):
                return StorySession(
                    story_emit,
                    ScriptClient(cfg.story_script_model, cfg.story_script_max_tokens,
                                 cfg.story_script_timeout_s),
                    ImageClient(cfg.story_image_model, cfg.story_image_size,
                                cfg.story_image_timeout_s),
                    StoryCache(cfg.story_cache_dir, cfg.story_cache_max_mb),
                    tts_factory, cfg)

            vs.set_story_session(story_factory)
            # web-074：LLM 意图兜底注入（正则未中且非明显问答的模糊表达才调到，~1s）
            vs.set_story_intent_classifier(
                lambda text: classify_intent_llm(text, model=cfg.story_script_model))
        return vs

    return make


def register_voice_ws(app, cfg: KioskConfig, session_factory=None) -> None:
    factory = session_factory or _default_session_factory(cfg)

    @app.websocket("/ws/voice")
    async def voice_ws(ws: WebSocket):
        # 浏览器 WebSocket 不能设自定义头 → token 走 query 参数
        if cfg.token and ws.query_params.get("token") != cfg.token:
            await ws.close(code=4401)
            return
        await ws.accept()
        loop = asyncio.get_running_loop()
        out_q: asyncio.Queue = asyncio.Queue()

        def emit(ev: dict) -> None:      # 任意线程可调（工作线程/SDK 回调线程）
            loop.call_soon_threadsafe(out_q.put_nowait, ev)

        try:
            session = factory(emit)
        except Exception as e:
            logger.warning("会话创建失败: %s", e)
            await ws.send_text(json.dumps(
                {"type": "error", "code": "init_failed",
                 "message": "服务初始化中，请稍后再试"}, ensure_ascii=False))
            await ws.close(code=1013)
            return

        async def forwarder() -> None:   # 唯一发送点（避免并发 send）
            while True:
                ev = await out_q.get()
                if ev is None:
                    return
                if ev.get("type") == "audio":
                    await ws.send_bytes(ev["pcm"])
                else:
                    await ws.send_text(json.dumps(ev, ensure_ascii=False))

        fwd = asyncio.create_task(forwarder())
        ask_task: asyncio.Task | None = None
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    session.feed_audio(msg["bytes"])   # web-011：PCM 上行→VAD/FSM/ASR
                    continue
                try:
                    data = json.loads(msg.get("text") or "{}")
                except json.JSONDecodeError:
                    out_q.put_nowait({"type": "error", "code": "bad_message"})
                    continue
                mtype = data.get("type")
                if mtype == "hello":
                    out_q.put_nowait({"type": "hello", "ok": True,
                                      "version": __version__,
                                      "voice": session.voice_enabled})
                elif mtype == "ping":
                    out_q.put_nowait({"type": "pong"})
                elif mtype == "ask":
                    # web-029：新问题永远打断旧问题（BroadcastSession 内串行化），
                    # 不再回 busy 错误
                    ask_task = asyncio.create_task(
                        asyncio.to_thread(session.ask, data.get("text", "")))
                elif mtype == "barge_in":
                    session.barge_in()
                elif mtype == "story_page":          # web-058：绘本翻页/收尾/退出
                    session.on_story_page(data.get("n", 0))
                elif mtype == "story_finish":
                    session.on_story_finish()
                elif mtype == "story_cancel":
                    session.on_story_cancel()
                else:
                    out_q.put_nowait({"type": "error", "code": "unknown_type"})
        except WebSocketDisconnect:
            pass
        finally:
            session.close()
            if ask_task is not None:
                try:
                    await asyncio.wait({ask_task}, timeout=2.0)
                except Exception:
                    pass
            out_q.put_nowait(None)
            try:
                await asyncio.wait_for(fwd, timeout=2.0)
            except Exception:
                fwd.cancel()
