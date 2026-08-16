/** 全拼键盘封装（web-023）：simple-keyboard + chinese 布局，对齐 1.5.x 设计稿。
 *  底排功能键：{shift}=Aa 大小写、{write}=手写、{space}=空格、{bksp}=退格、{finished}=完成。 */
import Keyboard from "simple-keyboard";
import layout from "simple-keyboard-layouts/build/layouts/chinese";
import "simple-keyboard/build/css/index.css";

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
  const keyboard = new Keyboard(container, {
    onChange: (input: string) => opts.onInput(input),
    onKeyPress: (button: string) => {
      if (button === "{shift}") {
        keyboard.setOptions({
          layoutName: keyboard.options.layoutName === "shift" ? "default" : "shift",
        });
      } else if (button === "{write}") {
        opts.onWrite();
      } else if (button === "{finished}") {
        opts.onFinished();
      }
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
    // 候选条由 chinese 布局内置（输入拼音自动出候选，点选上屏）
    theme: "simple-keyboard hg-theme-default hg-layout-default kiosk-keyboard",
  });
  return {
    destroy: () => keyboard.destroy(),
  };
}
