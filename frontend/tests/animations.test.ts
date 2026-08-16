// web-016：小鹿动画池逻辑（移植保真）
import { describe, expect, it } from "vitest";
import { ACTION_POOLS, ANIMATION, pickNext, poolOf } from "../src/components/deer/animations";

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
