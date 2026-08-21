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

  // web-070：候选条（键盘上方）
  const candBar = document.createElement("div");
  candBar.className = "pinyin-candidates";
  const kbHost = document.createElement("div");
  kbHost.className = "pinyin-kb-host";      // simple-keyboard 要求宿主带 class
  container.appendChild(candBar);
  container.appendChild(kbHost);

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
    syncing = true;
    keyboard.setInput(committed + buffer);
    syncing = false;
    opts.onInput(committed + buffer);
    renderCandidates();
  }

  function renderCandidates() {
    candBar.innerHTML = "";
    const cands = getCandidates(buffer);
    if (!buffer || !cands.length) {
      candBar.classList.add("empty");
      return;
    }
    candBar.classList.remove("empty");
    for (const c of cands) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `pinyin-cand pinyin-cand-${c.kind}`;
      b.textContent = c.text;
      b.addEventListener("click", () => pick(c));
      candBar.appendChild(b);
    }
  }

  renderCandidates();
  return {
    destroy: () => {
      keyboard.destroy();
      candBar.remove();                    // web-070 评审修复：自绘 DOM 随销毁清理——
      kbHost.remove();                     // 防宿主 clear() 重建后残留可点击陈旧候选/累积
    },
  };
}
