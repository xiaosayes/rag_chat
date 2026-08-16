/** 小鹿动画池（web-016）：移植自参考 Human.vue 的动画编排（纯逻辑，可单测）。 */

export const ANIMATION = Object.freeze({
  DAIJI: "daiji", // 待机
  SHUANGSHOUBIXIN: "shuangshoubixin", // 双手比心
  TIAOQI: "tiaoqi", // 跳起
  TIAOQIZHUANQUAN: "tiaoqizhuanquan", // 跳起转圈
  WANSHOUZHI: "wanshouzhi", // 玩手指
  YIHUO: "yihuo", // 疑惑
  ZUOSHOUHUISHOU: "zuoshouhuishou", // 左手挥手
  SHUANGSHOUTANSHOU_QUAN: "shuangshoutanshou+quankouxing", // 双手摊手（全口型）
  YOUSHOUTAIQI_QUAN: "youshoutaiqi+quankouxing", // 右手抬起（全口型）
  ZUOSHOUTAIQI_QUAN: "zuoshoutaiqi+quankouxing", // 左手抬起（全口型）
});

export type PoolName = "STANDBY" | "TALK";

export const ACTION_POOLS: Record<PoolName, string[]> = {
  STANDBY: [
    ANIMATION.SHUANGSHOUBIXIN,
    ANIMATION.TIAOQI,
    ANIMATION.TIAOQIZHUANQUAN,
    ANIMATION.WANSHOUZHI,
    ANIMATION.YIHUO,
    ANIMATION.ZUOSHOUHUISHOU,
  ],
  TALK: [
    ANIMATION.SHUANGSHOUTANSHOU_QUAN,
    ANIMATION.YOUSHOUTAIQI_QUAN,
    ANIMATION.ZUOSHOUTAIQI_QUAN,
  ],
};

export function poolOf(name: string): PoolName | undefined {
  for (const key of Object.keys(ACTION_POOLS) as PoolName[]) {
    if (ACTION_POOLS[key].includes(name)) return key;
  }
  return undefined;
}

/** 池内随机选一个（排除当前项，避免同动作连播）；rand 可注入（测试确定性）。 */
export function pickNext(pool: PoolName, current: string | undefined,
                         rand: () => number = Math.random): string {
  const candidates = ACTION_POOLS[pool].filter((n) => n !== current);
  return candidates[Math.floor(rand() * candidates.length)];
}
