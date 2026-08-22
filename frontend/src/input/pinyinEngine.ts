/** 连字拼音引擎（web-070）：词组级候选（tuzi→兔子/change→嫦娥）+ 单字候选。
 *  纯函数，不依赖 simple-keyboard 实例，供 pinyinKeyboard 自绘候选条调用。
 *  数据源：
 *  - 音节表/单字候选 = simple-keyboard-layouts chinese 布局 layoutCandidates（原单字链路同源）
 *  - 词库 = ../assets/pinyin_words.json（jieba 词频 × pypinyin 注音，scripts/build_pinyin_dict.py 生成） */
import layout from "simple-keyboard-layouts/build/layouts/chinese";
import dictJson from "../assets/pinyin_words.json";

const CHARS: Record<string, string> =
  (layout as unknown as { layoutCandidates?: Record<string, string> }).layoutCandidates ?? {};
const WORDS: Record<string, string[]> =
  (dictJson as { words: Record<string, string[]> }).words;
// web-071：首字母联想索引（nh→你好/年后/您好），词频+会话高频提权序
const INITIALS: Record<string, string[]> =
  (dictJson as { initials: Record<string, string[]> }).initials ?? {};
// web-072：单字母候选（h→和/好/会，jieba 单字词频序）
const LETTERS: Record<string, string[]> =
  (dictJson as { letters?: Record<string, string[]> }).letters ?? {};

const MAX_SYLLABLE_LEN = 6;                 // zhuang/shuang 级

export interface PinyinCandidate {
  text: string;                             // 候选文本（词或单字）
  eat: number;                              // 选中后从 buffer 头部消耗的字母数
  kind: "word" | "char";
}

/** buffer 的最长合法音节前缀（"tuzi"→"tu"），无则 ""。 */
export function longestSyllablePrefix(buffer: string): string {
  for (let len = Math.min(MAX_SYLLABLE_LEN, buffer.length); len > 0; len--) {
    if (CHARS[buffer.slice(0, len)] !== undefined) return buffer.slice(0, len);
  }
  return "";
}

/** DP 判定 buffer 能否完整切分为合法音节序列（词候选前置条件）。 */
function segmentable(buffer: string): boolean {
  const n = buffer.length;
  const dp = new Array<boolean>(n + 1).fill(false);
  dp[0] = true;
  for (let i = 0; i < n; i++) {
    if (!dp[i]) continue;
    for (let len = 1; len <= MAX_SYLLABLE_LEN && i + len <= n; len++) {
      if (CHARS[buffer.slice(i, i + len)] !== undefined) dp[i + len] = true;
    }
  }
  return dp[n];
}

/** 候选（web-070 连字 / web-071 首字母联想）：
 *  buffer 可完整切分为音节 → 全拼词候选在前、单字候选（最长音节前缀）随后；
 *  不可完整切分（如 nh）→ 首字母联想词优先、单字候选兑底。 */
export function getCandidates(buffer: string, max = 30): PinyinCandidate[] {
  if (!buffer) return [];
  // web-072：单字母即出高频字候选（h→和/好/会…）；a/e/o 等本身是音节的走原路径
  if (buffer.length === 1 && CHARS[buffer] === undefined) {
    return (LETTERS[buffer] ?? [])
      .map((text) => ({ text, eat: 1, kind: "char" as const }))
      .slice(0, max);
  }
  const out: PinyinCandidate[] = [];
  const seg = segmentable(buffer);
  if (seg) {
    const ws = WORDS[buffer];
    if (ws) {
      for (const w of ws) out.push({ text: w, eat: buffer.length, kind: "word" });
    }
  }
  if (!seg && buffer.length >= 2) {
    for (const w of INITIALS[buffer] ?? []) {
      out.push({ text: w, eat: buffer.length, kind: "word" });
    }
  }
  const p = longestSyllablePrefix(buffer);
  if (p) {
    for (const ch of (CHARS[p] ?? "").split(" ")) {
      if (ch) out.push({ text: ch, eat: p.length, kind: "char" });
    }
  }
  return out.slice(0, max);
}
