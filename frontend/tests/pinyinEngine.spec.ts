// web-070：连字拼音引擎——词组级候选（tuzi→兔子）+ 单字候选（最长音节前缀）
import { describe, expect, it } from "vitest";
import { getCandidates, longestSyllablePrefix } from "../src/input/pinyinEngine";

describe("pinyinEngine.getCandidates", () => {
  it("词组候选在前：tuzi → 兔子(eat=4, word)", () => {
    const c = getCandidates("tuzi");
    expect(c[0]).toEqual({ text: "兔子", eat: 4, kind: "word" });
  });

  it("词候选后接单字候选：tuzi 也含 土(eat=2, char)", () => {
    const c = getCandidates("tuzi");
    const chars = c.filter((x) => x.kind === "char");
    expect(chars[0]).toEqual({ text: "土", eat: 2, kind: "char" });  // layoutCandidates["tu"] 首字
    expect(chars.some((x) => x.text === "兔")).toBe(true);
  });

  it("单音节：tu → 土(eat=2, char) 且含 兔", () => {
    const c = getCandidates("tu");
    expect(c[0]).toEqual({ text: "土", eat: 2, kind: "char" });
    expect(c.some((x) => x.text === "兔")).toBe(true);
  });

  it("歧切分：xian 同时含词 西安(word) 与字 先(char)", () => {
    const c = getCandidates("xian");
    expect(c.some((x) => x.text === "西安" && x.kind === "word" && x.eat === 4)).toBe(true);
    expect(c.some((x) => x.text === "先" && x.kind === "char")).toBe(true);
  });

  it("成语：shouzhudaitu → 守株待兔(word)", () => {
    const c = getCandidates("shouzhudaitu");
    expect(c[0]).toEqual({ text: "守株待兔", eat: 12, kind: "word" });
  });

  it("嫦娥：change → 嫦娥(word)", () => {
    const c = getCandidates("change");
    expect(c[0]).toEqual({ text: "嫦娥", eat: 6, kind: "word" });
  });

  it("空串/单字母 → 空候选；纯辅音串走首字母联想", () => {
    expect(getCandidates("")).toEqual([]);
    expect(getCandidates("t")).toEqual([]);        // t 非音节且长度<2 不联想
    const zzz = getCandidates("zzz");              // 不可切分音节 → 首字母联想
    expect(zzz.some((x) => x.text === "自治州" && x.kind === "word")).toBe(true);
  });

  it("首字母联想：nh → 你好/年后/您好（词候选，eat=2，你好居首）", () => {
    const c = getCandidates("nh");
    expect(c.length).toBeGreaterThan(0);
    expect(c[0]).toEqual({ text: "你好", eat: 2, kind: "word" });   // 会话高频提权
    expect(c.some((x) => x.text === "年后")).toBe(true);
    expect(c.some((x) => x.text === "您好")).toBe(true);
  });

  it("全拼词优先于首字母词：tuzi 首候选仍为全拼命中", () => {
    const c = getCandidates("tuzi");
    expect(c[0]).toEqual({ text: "兔子", eat: 4, kind: "word" });
  });
});

describe("pinyinEngine.longestSyllablePrefix", () => {
  it("取最长合法音节前缀", () => {
    expect(longestSyllablePrefix("tuzi")).toBe("tu");
    expect(longestSyllablePrefix("zhong")).toBe("zhong");
    expect(longestSyllablePrefix("zzz")).toBe("");
  });
});
