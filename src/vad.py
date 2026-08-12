"""silero VAD 流式封装（audit-ASR 需求2）：语音活动检测 + 流式分段状态机。

自持 ONNX 推理：**不 import silero_vad 包**（其 utils_vad 顶层 import torchaudio——
服务器 conda 环境无 torchaudio、本地 DLL 亦可能损坏，装了整个包也用不了），
仅定位 pip 包内置的 silero_vad.onnx（MIT 许可，随 silero-vad 轮子分发）+
onnxruntime 直接推理。实测 0.095ms/30ms 窗，纯 CPU 绰绰有余。

StreamVAD 分段语义随 silero VADIterator（久经考验的闪烁容忍策略）：
  - 概率 >= threshold → 进入语音候选；候选起点回探 pad_ms 预缓冲
  - 候选持续时长 >= min_speech → 发 confirmed_start（提前确认："嗯/啊"短音
    未达即被静音终结丢弃，不发任何事件 —— 需求2"避免嗯啊喂触发 ASR"）
  - 候选内连续静音 >= min_silence → 段结束：语音部分 >= min_speech 发
    ("segment", pcm)（尾部保留 pad_ms 气口），否则整段丢弃
  - 候选时长 >= max_speech → 无静音也强制切段（防卡死，需求2）
  - 段结束 reset 模型 LSTM 状态（段间决策独立）
"""
import importlib.util
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger

WINDOW_SAMPLES = 512   # silero v6 @16kHz 支持 256/512/768；512 = 32ms
CONTEXT_SAMPLES = 64   # 官方 OnnxWrapper 实证：每窗须前拼上窗前 64 采样作上下文
                       # （输入实为 576 采样）；缺失则窗界不连续、概率输出废掉（实测峰值 0.13）
STATE_DIM = 128        # silero v6 onnx LSTM 状态维度（get_inputs 实证 [2,N,128]）


def find_silero_model(model_path: str = "") -> Path:
    """定位 silero_vad.onnx：显式路径 > silero_vad pip 包内置 data 目录。

    find_spec 不执行模块（绕开 silero_vad/__init__ 的 torchaudio 导入）。
    """
    if model_path:
        p = Path(model_path)
        if not p.exists():
            raise FileNotFoundError(f"silero_vad 模型路径不存在: {p}")
        return p
    spec = importlib.util.find_spec("silero_vad")
    if spec and spec.submodule_search_locations:
        p = Path(list(spec.submodule_search_locations)[0]) / "data" / "silero_vad.onnx"
        if p.exists():
            return p
    raise FileNotFoundError(
        "未找到 silero_vad.onnx：请 pip install silero-vad（模型随包内置，离线可用），"
        "或在 .env 设置 SILERO_VAD_MODEL_PATH 指向模型文件")


class SileroVadOnnx:
    """silero VAD ONNX 推理（stateful LSTM，逐 32ms 窗返回语音概率）。"""

    def __init__(self, model_path: str = "", sample_rate: int = 16000):
        import numpy as np
        import onnxruntime as ort

        self._np = np
        self.sample_rate = sample_rate
        self._sess = ort.InferenceSession(
            str(find_silero_model(model_path)), providers=["CPUExecutionProvider"])
        self._sr = np.array(sample_rate, dtype=np.int64)
        self.reset_states()

    def reset_states(self) -> None:
        self._state = self._np.zeros((2, 1, STATE_DIM), dtype=self._np.float32)
        self._context = self._np.zeros(CONTEXT_SAMPLES, dtype=self._np.float32)

    def prob_window(self, samples) -> float:
        """samples: float32[512]（[-1,1] 归一化）→ 语音概率 [0,1]。"""
        x = self._np.concatenate([self._context, samples])[None, :]
        out, state = self._sess.run(
            None, {"input": x, "state": self._state, "sr": self._sr})
        self._state = state
        self._context = x[0, -CONTEXT_SAMPLES:]
        return float(out[0][0])

    def prob_pcm(self, pcm: bytes) -> float:
        """16k 16bit little-endian PCM 一窗（1024 字节）→ 语音概率。"""
        samples = self._np.frombuffer(pcm, dtype=self._np.int16).astype(self._np.float32) / 32768.0
        return self.prob_window(samples)


class StreamVAD:
    """流式 VAD 分段状态机：feed(PCM) → 事件列表。

    事件：
      ("confirmed_start", None) —— 候选语音已达 min_speech，消费端应开 ASR 会话
                                    并 take_pending() 喂入自段首（含 pad）起的缓冲
      ("segment", pcm_bytes)    —— 语音段结束（静音超时或 max_speech 强制），
                                    payload 为完整段（首 pad + 语音 + 尾 pad）
    """

    def __init__(self, model, *, threshold: float = 0.5, min_speech_ms: int = 400,
                 min_silence_ms: int = 800, pad_ms: int = 200,
                 max_speech_s: int = 15, sample_rate: int = 16000):
        self.model = model
        self.threshold = threshold
        self.sample_rate = sample_rate
        self._win_bytes = WINDOW_SAMPLES * 2
        self._min_speech = int(sample_rate * min_speech_ms / 1000)
        self._min_silence = int(sample_rate * min_silence_ms / 1000)
        self._pad = int(sample_rate * pad_ms / 1000)
        self._max_speech = int(sample_rate * max_speech_s)
        self._raw = bytearray()      # 未凑满一窗的残字节
        self._pre = bytearray()      # idle 态 pad 环形预缓冲
        self._seg = bytearray()      # 候选段缓冲（首 pad + 已收语音/静音）
        self._pre_seeded = 0         # 段首播种的 pad 采样数
        self._read = 0               # take_pending 已读位置
        self._in_speech = False
        self._confirmed = False
        self._speech_samples = 0     # 候选起点至今音频量（含闪烁静音）
        self._silence_run = 0        # 候选内连续静音

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def confirmed(self) -> bool:
        return self._confirmed

    def feed(self, pcm: bytes) -> List[Tuple[str, Optional[bytes]]]:
        events: List[Tuple[str, Optional[bytes]]] = []
        self._raw += pcm[: len(pcm) // 2 * 2]  # 奇数长度截断（防御）
        while len(self._raw) >= self._win_bytes:
            win = bytes(self._raw[: self._win_bytes])
            del self._raw[: self._win_bytes]
            events.extend(self._process_window(win))
        return events

    def take_pending(self) -> bytes:
        """候选段自上次读取以来的新增字节（confirmed 后供 ASR 增量喂入）。"""
        if self._read >= len(self._seg):
            return b""
        b = bytes(self._seg[self._read:])
        self._read = len(self._seg)
        return b

    # ---------- 内部 ----------

    def _process_window(self, win: bytes) -> List[Tuple[str, Optional[bytes]]]:
        events: List[Tuple[str, Optional[bytes]]] = []
        prob = self.model.prob_pcm(win)
        n = WINDOW_SAMPLES
        if not self._in_speech:
            if prob < self.threshold:
                # idle：维护 pad 环形预缓冲（段首回探用）
                self._pre += win
                if len(self._pre) > self._pad * 2:
                    del self._pre[: len(self._pre) - self._pad * 2]
                return events
            # 进入语音候选：播种首 pad
            self._in_speech = True
            self._confirmed = False
            self._speech_samples = 0
            self._silence_run = 0
            self._seg = bytearray(self._pre[-self._pad * 2:]) if self._pad else bytearray()
            self._pre_seeded = len(self._seg) // 2
            self._read = 0

        self._seg += win
        self._speech_samples += n
        if prob >= self.threshold:
            self._silence_run = 0
        else:
            self._silence_run += n

        # 确认用"有效语音时长"（连续静音不计）：短促"嗯"后接静音不会误确认
        if not self._confirmed and self._speech_samples - self._silence_run >= self._min_speech:
            self._confirmed = True
            events.append(("confirmed_start", None))

        if self._silence_run >= self._min_silence:
            # 段结束：语音部分（不含首 pad 与尾静音）达标才成段
            speech_part = len(self._seg) // 2 - self._pre_seeded - self._silence_run
            if speech_part >= self._min_speech:
                keep = len(self._seg) - self._silence_run * 2 + self._pad * 2
                keep = max(0, min(keep, len(self._seg)))
                events.append(("segment", bytes(self._seg[:keep])))
            self._end_candidate()
        elif self._speech_samples >= self._max_speech:
            # 强制切段（防卡死）：含当前窗，无尾 pad 修剪
            events.append(("segment", bytes(self._seg)))
            self._end_candidate()
        return events

    def _end_candidate(self) -> None:
        self._in_speech = False
        self._confirmed = False
        self._seg = bytearray()
        self._pre = bytearray()
        self._read = 0
        self._speech_samples = 0
        self._silence_run = 0
        if hasattr(self.model, "reset_states"):
            self.model.reset_states()


def try_create_vad(*, model_path: str = "", threshold: float = 0.5,
                   min_speech_ms: int = 400, min_silence_ms: int = 800,
                   pad_ms: int = 200, max_speech_s: int = 15,
                   sample_rate: int = 16000) -> Optional[StreamVAD]:
    """创建 StreamVAD；模型缺失/onnxruntime 不可用 → None（调用方降级，不崩）。"""
    try:
        return StreamVAD(
            SileroVadOnnx(model_path, sample_rate),
            threshold=threshold, min_speech_ms=min_speech_ms,
            min_silence_ms=min_silence_ms, pad_ms=pad_ms,
            max_speech_s=max_speech_s, sample_rate=sample_rate)
    except Exception as e:
        logger.warning(f"VAD 初始化失败（语音助手降级为手动模式）: {e}")
        return None
