// web-046：前端展示层 Markdown 清洗——剥离 ** 等语法符号（观感/整洁），
// 同时严格保护必要符号（小数/日期/区间/百分比/货币/列表序号），保证可读性与连续性。
import { describe, expect, it } from "vitest";
import { cleanForDisplay } from "../src/utils/textClean";

describe("cleanForDisplay: 剥离 Markdown 语法", () => {
  it("成对 ** 粗体标记", () => {
    expect(cleanForDisplay("欢迎来到**家博会**！")).toBe("欢迎来到家博会！");
  });

  it("未配对 **（流式分段切断）", () => {
    expect(cleanForDisplay("**意大利风格沙发")).toBe("意大利风格沙发");
    expect(cleanForDisplay("沙发展馆（精品）**")).toBe("沙发展馆（精品）");
  });

  it("斜体 *text* 与行首项目符号，但保护乘法式", () => {
    expect(cleanForDisplay("这是*斜体*内容")).toBe("这是斜体内容");
    expect(cleanForDisplay("* 第一项")).toBe("第一项");
    expect(cleanForDisplay("面积 3*5=15 平方米")).toBe("面积 3*5=15 平方米");
  });

  it("标题/引用/行内代码/链接/图片", () => {
    expect(cleanForDisplay("## 展品介绍")).toBe("展品介绍");
    expect(cleanForDisplay("> 引用一句")).toBe("引用一句");
    expect(cleanForDisplay("命令 `npm run dev` 启动")).toBe("命令 npm run dev 启动");
    expect(cleanForDisplay("[点击查看](http://x.com)")).toBe("点击查看");
    expect(cleanForDisplay("![示意图](a.png)")).toBe("示意图");
  });

  it("删除线与下划线粗体", () => {
    expect(cleanForDisplay("~~作废~~保留")).toBe("作废保留");
    expect(cleanForDisplay("__重点__提示")).toBe("重点提示");
  });

  it("标记剥离后空白连续：压缩连续空格、标点不留前导空格", () => {
    expect(cleanForDisplay("沙发  **  生活馆 ，不错")).toBe("沙发 生活馆，不错");
  });

  it("真实答案样例（截图 22）连续成段", () => {
    const raw = "1. **沙发生活馆（精品）** - **位置**：A区 2.2号馆 - **推荐理由**：这里汇聚了许多国内外知名品牌";
    expect(cleanForDisplay(raw)).toBe(
      "1. 沙发生活馆（精品） - 位置：A区 2.2号馆 - 推荐理由：这里汇聚了许多国内外知名品牌",
    );
  });
});

describe("cleanForDisplay: 不误伤必要符号（可展示性/可读性）", () => {
  it.each([
    "A区 2.2号馆",                 // 小数
    "全长 1.5小时",                // 小数
    "2025年3月18日—21日",          // 日期区间（波浪/连接号）
    "3~5个推荐",                   // 波浪区间
    "50%折扣",                     // 百分比
    "价格¥199元",                  // 货币
    "1. 沙发馆 2. 家具馆",          // 列表序号保留（展示可读性）
    "（精品）展区《导览图》",        // 中文括号书名号
    "嗯……我想想",                  // 省略号
    "第2期展览：门票19.9元",        // 期号+价格小数
  ])("原样保留: %s", (s) => {
    expect(cleanForDisplay(s)).toBe(s);
  });

  it("换行结构保留", () => {
    expect(cleanForDisplay("第一行。\n第二行。")).toBe("第一行。\n第二行。");
  });
});
