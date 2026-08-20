/** 全局状态（web-014）：加载进度 / 客户端配置 / 预设问题池。 */
import { defineStore } from "pinia";
import { api, type KioskConfig } from "../api/client";

/** 预设兜底池（服务器不可达时的离线副本，与 kiosk_server/presets.py 同源） */
export const FALLBACK_PRESETS = [
  "志愿者报名条件", "志愿者的工作内容", "如何办证，办证须知", "图书丢失、污损怎么办？",
  "有什么不能带的东西吗？", "楼层介绍", "湖南省少年儿童图书馆的简介", "湖南省少年儿童图书馆开放时间",
  "借书证怎么办理？", "一次可以借几本书？", "借书期限是多久？", "逾期还书会怎么样？",
  "图书馆里有无线网络吗？", "自习室怎么预约？", "周末开门吗？", "儿童阅览室在几楼？",
  "给我讲个嫦娥奔月的故事",   // web-062：绘本引导入口（与服务端缺省池同源）
];

export const useAppStore = defineStore("app", {
  state: () => ({
    loadProgress: 0,          // 0~100，启动页进度（模型 80% + 环境 20%）
    modelReady: false,
    config: null as KioskConfig | null,
    presetPool: [...FALLBACK_PRESETS] as string[],
    voiceEnabled: false,      // hello 握手后由服务端 voice 标志更新
    homeAfterS: 150,          // 空闲回首页（/api/config 下发覆盖）
    refreshAfterS: 300,       // 空闲自刷新
  }),
  actions: {
    async bootstrap() {
      try {
        this.config = await api.config();
        this.homeAfterS = this.config.idle_home_s;
        this.refreshAfterS = this.config.idle_refresh_s;
      } catch {
        /* 离线兜底：用默认值 */
      }
      try {
        const { questions } = await api.presets();
        if (questions.length) this.presetPool = questions;
      } catch {
        /* 兜底池 */
      }
    },
  },
});
