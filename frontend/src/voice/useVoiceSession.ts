/** 语音会话编排（web-020）：WS 事件 → UI 状态 / 播放器 / 小鹿动画。
 *  每轮 PCM 端侧缓存（重播零网络）；greeting 音频不入轮缓存。 */
import { reactive, ref } from "vue";
import { PcmPlayer } from "../audio/player";
import { VoiceWsClient } from "./VoiceWsClient";

export interface ChatItem {
  type: "me" | "deer";
  text: string;
  status: 0 | 1;                 // 0=等待中（波形 loading），1=定稿
  pcm?: ArrayBuffer[];           // 该轮 PCM 缓存（端侧重播）
  durationS?: number;
}

export type UiMode = "standby" | "listen" | "broadcast" | "await_broadcast";

export interface VoiceSessionDeps {
  client?: VoiceWsClient;                     // 缺省自建（测试注入假件）
  player?: PcmPlayer;                         // 缺省自建（测试注入假件）
  onTalkChange?: (talking: boolean) => void;   // 小鹿 TALK/STANDBY
  onActivity?: () => void;                     // 服务端活动（空闲计时复位）
  onClose?: () => void;
}

export function useVoiceSession(deps: VoiceSessionDeps) {
  const mode = ref<UiMode>("standby");
  const statusText = ref("");
  const voiceReady = ref(false);
  const recording = ref(false);
  const speaking = ref(false);   // web-035：检测到说话声（首个 asr_partial）→ 胶囊才显示“正在录入语音”
  const chatHistory = reactive<ChatItem[]>([]);
  const player = deps.player ?? new PcmPlayer(24000);
  /** 当前收集中的轮次 PCM 缓存 */
  let collecting: ArrayBuffer[] | null = null;
  let currentDeer: ChatItem | null = null;

  function lastMe(): ChatItem | null {
    const last = chatHistory[chatHistory.length - 1];
    return last?.type === "me" ? last : null;
  }

  function onEvent(ev: any) {
    deps.onActivity?.();
    // 注意：player 为本闭包内自建/注入实例
    switch (ev.type) {
      case "state":
        mode.value = ev.mode ?? mode.value;
        statusText.value = ev.status_text ?? "";
        if (ev.mode === "standby" || ev.mode === "listen") speaking.value = false;  // 重新聆听/待机（web-035）
        break;
      case "asr_partial": {
        speaking.value = true;   // 有声音被识别到（web-035）
        // 边说边上屏：更新/创建用户气泡
        const me = lastMe();
        if (me && me.status === 1 && me.text !== ev.text) {
          me.text = ev.text;
        } else if (!me) {
          chatHistory.push({ type: "me", text: ev.text, status: 1 });
        }
        break;
      }
      case "greet":
        break;                    // 应答语走音频流 + 状态行（不写对话框，对齐后端语义）
      case "answer_start":
        speaking.value = false;  // 进入作答（web-035）
        // 推入后回取 reactive 代理再持有——直接改原对象不触发视图更新（web-021 实测）
        chatHistory.push({ type: "deer", text: "", status: 0 });
        currentDeer = chatHistory[chatHistory.length - 1];
        break;
      case "answer_chunk":
        if (currentDeer) {
          currentDeer.status = 1;
          currentDeer.text += ev.text;
        }
        break;
      case "audio_start":
        playerStart(ev.greeting ? null : ev.turn);
        break;
      case "audio_end":
        finishAudio(ev);
        break;
      case "playback_cancel":
        player.stop();
        if (!ev.greeting) deps.onTalkChange?.(false);
        collecting = null;
        break;
      case "answer_end":
        if (currentDeer) {
          currentDeer.status = 1;
          currentDeer.text = ev.full_text || currentDeer.text;
        }
        currentDeer = null;
        collecting = null;
        break;
      case "error":
        statusText.value = ev.message ?? "服务异常";
        break;
    }
  }

  function playerStart(turn: number | null) {
    player.start();
    deps.onTalkChange?.(true);
    collecting = turn === null ? null : [];
  }

  function finishAudio(ev: any) {
    if (!ev.greeting && currentDeer && collecting) {
      currentDeer.pcm = collecting;
      currentDeer.durationS =
        collecting.reduce((s, b) => s + b.byteLength / 2, 0) / 24000;
    }
    collecting = null;
    deps.onTalkChange?.(false);
  }

  function onAudio(buf: ArrayBuffer) {
    player.push(buf);
    collecting?.push(buf.slice(0));      // 拷贝入缓存（播放器不持有）
  }

  const client = deps.client ?? new VoiceWsClient({
    onOpen: (v) => { voiceReady.value = v; },
    onEvent,
    onAudio,
    onClose: () => { deps.onClose?.(); },
  });

  // ---------- 对外动作 ----------

  function connect() {
    client.connect();
  }

  function askText(q: string) {
    const text = q.trim();
    if (!text) return;
    chatHistory.push({ type: "me", text, status: 1 });
    client.ask(text);
  }

  /** 常开推流启停（语音模式） */
  function setRecording(on: boolean) {
    recording.value = on;
  }

  function barge() {
    client.bargeIn();
  }

  /** 端侧重播（零网络）：player 直接排播缓存 PCM；fromS 偏移按整帧粒度丢弃前缀（web-026） */
  function replay(item: ChatItem, fromS = 0) {
    if (!item.pcm?.length) return 0;
    player.stop();
    player.start();
    let skipped = 0;
    for (const buf of item.pcm) {
      const dur = buf.byteLength / 2 / 24000;
      if (skipped + dur <= fromS) {          // 整帧落在偏移前 → 丢弃
        skipped += dur;
        continue;
      }
      player.push(buf.slice(0));
    }
    deps.onTalkChange?.(true);
    return Math.max(0, (item.durationS ?? 0) - skipped);
  }

  function resetChat() {
    chatHistory.splice(0, chatHistory.length);
    currentDeer = null;
    collecting = null;
  }

  return {
    mode, statusText, voiceReady, recording, speaking, chatHistory,
    onEvent, onAudio,
    connect, askText, barge, replay, setRecording, resetChat,
    client, player,
  };
}
