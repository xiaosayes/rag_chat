/** 全拼键盘封装（web-023 初版 / web-070 连字拼音改写）：simple-keyboard + chinese 布局。
 *  web-070：自绘候选条 + 自管 committed/buffer 输入模型——
 *  词组级候选（tuzi→兔子、change→嫦娥，pinyinEngine + 2.1MB 词库）在前，
 *  单字候选（最长音节前缀）在后；空格有 buffer 时=选第一个候选（IME 惯例）。
 *  底排功能键：{shift}=Aa 大小写、{write}=手写、{space}=空格、{bksp}=退格、{finished}=完成。 */
import Keyboard from "simple-keyboard";
import layout from "simple-keyboard-layouts/build/layouts/chinese";
import "simple-keyboard/build/css/index.css";
import { getCandidates, type PinyinCandidate } from "./pinyinEngine";

export interface PinyinKeyboardOptions {
  onInput: (text: string) => void;        // 已上屏文本变化（含候选选字）
  onWrite: () => void;                    // 切手写
  onFinished: () => void;                 // 完成（收起键盘）
}

export interface PinyinKeyboard {
  destroy: () => void;
}

export function createPinyinKeyboard(
  container: HTMLElement,
  opts: PinyinKeyboardOptions,
): PinyinKeyboard {
  const chinese = { ...layout };
  // 设计稿 1.5.0：Aa 键在三排首位；底排 手写/空格/退格/完成
  chinese.layout = {
    default: [
      "q w e r t y u i o p",
      "a s d f g h j k l",
      "{shift} z x c v b n m ， 。",
      "{write} {space} {bksp} {finished}",
    ],
    shift: [
      "Q W E R T Y U I O P",
      "A S D F G H J K L",
      "{shift} Z X C V B N M ， 。",
      "{write} {space} {bksp} {finished}",
    ],
  };
  delete (chinese as { layoutCandidates?: unknown }).layoutCandidates;   // web-070：自绘候选条

  // web-070：候选条（键盘上方）；web-071：单行+「更多▼」浮层展开（候选不再挤占键盘）
  // 包含块由 CSS 钉住（.simple-keyboard-host{position:relative}，KeyboardInput.vue）
  const candBar = document.createElement("div");
  candBar.className = "pinyin-candidates";
  const overlay = document.createElement("div");
  overlay.className = "pinyin-cand-overlay";
  overlay.style.display = "none";
  const kbHost = document.createElement("div");
  kbHost.className = "pinyin-kb-host";      // simple-keyboard 要求宿主带 class
  container.appendChild(overlay);
  container.appendChild(candBar);
  container.appendChild(kbHost);
  let expanded = false;
  let currentCands: PinyinCandidate[] = [];

  // 自管输入模型：committed=已上屏文本，buffer=未上屏拼音字母
  let committed = "";
  let buffer = "";
  let syncing = false;                    // setInput 触发的 onChange 重入护栏

  const keyboard = new Keyboard(kbHost, {
    onChange: () => {                     // 物理键触发的内部 input 变化不作准——
      if (syncing) return;                // 权威值=committed+buffer，onKeyPress 里统一回写
    },
    onKeyPress: (button: string) => {
      if (button === "{shift}") {
        keyboard.setOptions({
          layoutName: keyboard.options.layoutName === "shift" ? "default" : "shift",
        });
        return;
      }
      if (button === "{write}") { opts.onWrite(); return; }
      if (button === "{finished}") { opts.onFinished(); return; }
      if (button === "{bksp}") {
        if (buffer) buffer = buffer.slice(0, -1);
        else committed = committed.slice(0, -1);
      } else if (button === "{space}") {
        const cands = getCandidates(buffer);
        if (buffer && cands.length) {
          pick(cands[0]);                 // IME 惯例：空格=选第一个候选
          return;                         // pick 内已 sync
        }
        committed += buffer + " ";        // 无候选：buffer 原样上屏 + 空格
        buffer = "";
      } else if (/^[a-z]$/i.test(button)) {
        buffer += button.toLowerCase();   // shift 层大写字母归一
      } else if (button === "，" || button === "。") {
        committed += buffer + button;     // 标点前若有未成词拼音，原样带上屏
        buffer = "";
      } else {
        return;                           // 其余功能键忽略
      }
      sync();
    },
    ...chinese,
    layoutName: "default",
    display: {
      "{bksp}": "⌫",
      "{write}": "手写",
      "{space}": "空格",
      "{finished}": "完成",
      "{shift}": "Aa",
    },
    theme: "simple-keyboard hg-theme-default hg-layout-default kiosk-keyboard",
  });

  function pick(c: PinyinCandidate) {
    committed += c.text;
    buffer = buffer.slice(c.eat);         // 余下字母继续组词（连打）
    sync();
  }

  function sync() {
    expanded = false;                        // web-071 评审修复 M1：击键改 buffer 即收起浮层
    syncing = true;
    keyboard.setInput(committed + buffer);
    syncing = false;
    opts.onInput(committed + buffer);
    renderCandidates();
  }

  // web-071：单行容量估算（字号 48px + 内边距 + 间距），超出部分进「更多」浮层；
  // 有溢出时按较窄预算重排，给「更多▼」钮预留位置（防其被裁剪）
  // web-071 评审修复 C3：估算对齐实际钮宽上浮冗余；「更多▼」场景预算收窄防其被裁；
  // web-072 续：字号 40px 后重校（40·len+padding52+border4 → 44·len+64 上浮）
  const ROW_BUDGET = 1000;
  const ROW_BUDGET_WITH_MORE = 780;

  function splitRow(cands: PinyinCandidate[], budget: number): PinyinCandidate[] {
    const row: PinyinCandidate[] = [];
    let used = 0;
    for (const c of cands) {
      const w = c.text.length * 44 + 64;
      if (row.length && used + w > budget) break;
      row.push(c);
      used += w + 14;
    }
    return row;
  }

  function makeCandButton(c: PinyinCandidate): HTMLButtonElement {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `pinyin-cand pinyin-cand-${c.kind}`;
    b.textContent = c.text;
    b.addEventListener("click", () => {
      expanded = false;
      pick(c);
    });
    return b;
  }

  function renderCandidates() {
    currentCands = buffer ? getCandidates(buffer) : [];
    candBar.innerHTML = "";
    overlay.innerHTML = "";
    if (!currentCands.length) {
      expanded = false;
      overlay.style.display = "none";
      candBar.classList.add("empty");
      return;
    }
    candBar.classList.remove("empty");
    let row = splitRow(currentCands, ROW_BUDGET);
    if (currentCands.length > row.length) {
      row = splitRow(currentCands, ROW_BUDGET_WITH_MORE);   // 给「更多▼」留位
    }
    for (const c of row) candBar.appendChild(makeCandButton(c));
    if (currentCands.length > row.length) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "pinyin-cand pinyin-cand-more";
      more.textContent = expanded ? "收起 ▲" : "更多 ▼";
      more.addEventListener("click", () => {
        expanded = !expanded;
        renderCandidates();
      });
      candBar.appendChild(more);
    }
    if (expanded) {
      for (const c of currentCands) overlay.appendChild(makeCandButton(c));
      fitOverlay();                        // web-072：运行时量高防裁（评审 30.png 顶行被切）
      overlay.style.display = "flex";
    } else {
      overlay.style.display = "none";
    }
  }

  // web-072 续：浮层改 fixed 定位（相对视口，逃脱 .panel overflow 裁剪——30.png 顶行被切/
  // 太窄两级反馈），底部锚在候选条上方、横向对齐键盘面板，目标高 540px（≈4 行+滚动），
  // 顶不少于 280px（给鹿景/标题留视觉余量）
  function fitOverlay() {
    const r = container.getBoundingClientRect();
    overlay.style.left = `${r.left}px`;
    overlay.style.width = `${r.width}px`;
    overlay.style.bottom = `${window.innerHeight - r.top + 12}px`;
    const target = 540;
    const top = Math.max(280, r.top - 12 - target);
    overlay.style.maxHeight = `${Math.max(220, r.top - 12 - top)}px`;
  }

  renderCandidates();
  return {
    destroy: () => {
      keyboard.destroy();
      candBar.remove();                    // web-070 评审修复：自绘 DOM 随销毁清理——
      kbHost.remove();                     // 防宿主 clear() 重建后残留可点击陈旧候选/累积
      overlay.remove();                    // web-071 评审修复 C2：第三个自绘 DOM 一并清理
    },
  };
}
