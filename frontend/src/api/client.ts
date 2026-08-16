/** 薄层 REST 客户端（web-014）：baseURL 走 VITE_API_URL，dev 经 vite proxy。 */

export interface KioskConfig {
  persona: string;
  wake_words: string[];
  tts_enabled: boolean;
  idle_home_s: number;
  idle_refresh_s: number;
}

export interface Health {
  ok: boolean;
  service: string;
  version: string;
  kb: string;
  tts: boolean;
  vad: string;
}

const BASE = (import.meta.env.VITE_API_URL as string) || "";

async function getJson<T>(path: string, timeoutMs = 8000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(`${BASE}${path}`, { signal: ctrl.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return (await resp.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

async function postJson<T>(path: string, body: unknown, timeoutMs = 15000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return (await resp.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  health: () => getJson<Health>("/api/health"),
  config: () => getJson<KioskConfig>("/api/config"),
  presets: () => getJson<{ questions: string[] }>("/api/presets"),
  /** 手写 OCR（web-023）：画布 PNG base64 → 文本；失败抛错 */
  ocr: (imageBase64: string) =>
    postJson<{ text: string }>("/api/ocr", { image_base64: imageBase64 }),
};
