// web-016：小鹿动画池逻辑（移植保真）
import { describe, expect, it } from "vitest";
import { ACTION_POOLS, ANIMATION, matchAccentAction, pickNext, poolOf } from "../src/components/deer/animations";

describe("deer animations", () => {
  it("池内容与参考一致", () => {
    expect(ACTION_POOLS.STANDBY).toHaveLength(6);
    expect(ACTION_POOLS.TALK).toHaveLength(3);
    expect(ACTION_POOLS.TALK.every((n) => n.includes("quankouxing"))).toBe(true);
    expect(ACTION_POOLS.STANDBY).toContain(ANIMATION.DAIJI === "daiji" ? "shuangshoubixin" : "");
  });

  it("poolOf 归类", () => {
    expect(poolOf("tiaoqi")).toBe("STANDBY");
    expect(poolOf("youshoutaiqi+quankouxing")).toBe("TALK");
    expect(poolOf("不存在")).toBeUndefined();
  });

  it("pickNext 排除当前项且确定性（注入 rand）", () => {
    expect(pickNext("TALK", ACTION_POOLS.TALK[0], () => 0)).toBe(ACTION_POOLS.TALK[1]);
    const picked = pickNext("STANDBY", ACTION_POOLS.STANDBY[0], () => 0.99);
    expect(picked).not.toBe(ACTION_POOLS.STANDBY[0]);
    expect(ACTION_POOLS.STANDBY).toContain(picked);
  });
});

describe("主题点缀动作映射（web-039）", () => {
  it("高置信主题命中（问题/答案分域）", () => {
    expect(matchAccentAction("谢谢你的帮助")).toBe(ANIMATION.SHUANGSHOUBIXIN);
    expect(matchAccentAction("下次再来，再见")).toBe(ANIMATION.ZUOSHOUHUISHOU);
    expect(matchAccentAction("图书馆简介", "抱歉，我暂时没有找到")).toBe(ANIMATION.YIHUO);
    expect(matchAccentAction("恭喜你借书成功")).toBe(ANIMATION.TIAOQIZHUANQUAN);
  });

  it("否定语境过滤：不用谢不触发比心", () => {
    expect(matchAccentAction("不用谢，这是我应该做的")).toBeUndefined();
    expect(matchAccentAction("不客气，有问题随时问我")).toBeUndefined();
  });

  it("答案域隔离：小鹿说「不客气」不扼杀用户感谢主题", () => {
    // 用户问题含「谢谢」→ 比心；答案侧的客套话不影响（scope=q 只看问题）
    expect(matchAccentAction("谢谢你的解答", "不客气，随时问我")).toBe(ANIMATION.SHUANGSHOUBIXIN);
  });

  it("无命中回退 undefined（维持随机池）", () => {
    expect(matchAccentAction("家博会几点开门")).toBeUndefined();
    expect(matchAccentAction("图书馆开放时间")).toBeUndefined();
    expect(matchAccentAction("")).toBeUndefined();
  });

  it("规则按序匹配，先中先得", () => {
    expect(matchAccentAction("谢谢你，再见")).toBe(ANIMATION.ZUOSHOUHUISHOU);
  });
});
