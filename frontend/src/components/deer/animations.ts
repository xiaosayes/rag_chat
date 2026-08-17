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

/** 主题点缀动作映射（web-039）：回答内容 → 点缀动作（高置信、保守收敛）。
 *  scope：q=看用户问题（用户意图类）；a=看小鹿回答（应答状态类）；qa=两者。
 *  deny 先于关键词判定（否定语境过滤）。未命中 undefined = 维持现有随机池
 * （用户确认的「无匹配则随机组合播放」兑底）。 */
export interface ThemeRule {
  action: string;
  scope: "q" | "a" | "qa";
  keywords: string[];
  deny?: string[];
}

export const THEME_RULES: readonly ThemeRule[] = Object.freeze([
  { action: ANIMATION.ZUOSHOUHUISHOU, scope: "qa", keywords: ["再见", "拜拜"] },
  { action: ANIMATION.SHUANGSHOUBIXIN, scope: "q", keywords: ["谢谢", "感谢"],
    deny: ["不用谢", "不谢"] },
  { action: ANIMATION.YIHUO, scope: "a",
    keywords: ["抱歉", "暂时没有", "没能找到", "不太确定"] },
  { action: ANIMATION.TIAOQIZHUANQUAN, scope: "qa", keywords: ["恭喜", "太好了", "棒极了"] },
]);

export function matchAccentAction(question: string, answer: string = ""): string | undefined {
  for (const r of THEME_RULES) {
    const hay = r.scope === "q" ? question : r.scope === "a" ? answer : `${question} ${answer}`;
    if (!hay) continue;
    if (r.deny?.some((d) => hay.includes(d))) continue;
    if (r.keywords.some((k) => hay.includes(k))) return r.action;
  }
  return undefined;
}
