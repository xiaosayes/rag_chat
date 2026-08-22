# -*- coding: utf-8 -*-
"""一次性生成前端连字拼音词库：jieba 词表（词频序）× pypinyin 注音 → JSON。
依赖（仅生成期，非运行时）：pip install jieba pypinyin
用法: python -X utf8 scripts/build_pinyin_dict.py
产出: frontend/src/assets/pinyin_words.json
  {"words": {全拼key: [词...]}, "initials": {首字母key: [词...]}}（均词频降序；words 每 key ≤8，initials 每 key ≤10）
"""
import json
import os
from pathlib import Path

import jieba
from pypinyin import Style, pinyin

OUT = Path(__file__).resolve().parent.parent / "frontend/src/assets/pinyin_words.json"
MIN_FREQ = 8          # 低频词不收，控制体积
MAX_WORD_LEN = 4      # 覆盖成语/短词（守株待兔）
MAX_PER_KEY = 8
MAX_PER_INITIAL = 10  # web-071：首字母联想每 key 候选上限

# web-071：会话/儿童高频词提权——jieba 词频来自新闻语料（「南海」压过「你好」），
# 一体机对话场景需将常用词排到联想前列
BOOST_WORDS = frozenset(
    "你好 您好 年后 谢谢 不客气 对不起 再见 请问 早上好 晚上好 新年快乐 "
    "故事 图书 图书馆 借书 还书 绘本 童话 寓言 成语 "
    "兔子 乌龟 嫦娥 公主 王子 恐龙 老虎 狐狸 猴子 大象 熊猫 小鸟 小鱼 小猫 小狗 "
    "妈妈 爸爸 爷爷 奶奶 姐姐 哥哥 弟弟 妹妹 老师 同学 小朋友 宝宝 孩子 "
    "生日 快乐 高兴 开心 漂亮 好看 好玩 喜欢 可爱 聪明 勇敢 "
    "苹果 香蕉 西瓜 牛奶 面包 米饭 月亮 太阳 星星 雪花 花儿 小草 大树".split())


def main() -> None:
    dt_path = os.path.join(os.path.dirname(jieba.__file__), "dict.txt")
    words: dict[str, list[tuple[int, str]]] = {}
    with open(dt_path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3:
                continue
            word, freq = parts[0], int(parts[1])
            if not (2 <= len(word) <= MAX_WORD_LEN) or freq < MIN_FREQ:
                continue
            if not all("一" <= ch <= "鿿" for ch in word):
                continue
            key = "".join(p[0] for p in pinyin(word, style=Style.NORMAL)).lower()
            if not key.isalpha():
                continue
            bucket = words.setdefault(key, [])
            if word not in (w for _, w in bucket):
                bucket.append((freq, word))
    def _rank(bucket):
        return [w for _, w in sorted(
            bucket, key=lambda t: -(t[0] + (10 ** 7 if t[1] in BOOST_WORDS else 0)))]

    out = {k: _rank(v)[:MAX_PER_KEY] for k, v in words.items()}
    # web-071：首字母联想索引（nh→你好/年后/您好）——按字取拼音首字母建键
    initials: dict[str, list[tuple[int, str]]] = {}
    for k, bucket in words.items():
        for freq, word in bucket:
            ikey = "".join(p[0][0] for p in pinyin(word, style=Style.NORMAL)).lower()
            if len(ikey) >= 2 and ikey.isalpha():
                ib = initials.setdefault(ikey, [])
                if word not in (w for _, w in ib):
                    ib.append((freq, word))
    out_initials = {k: _rank(v)[:MAX_PER_INITIAL] for k, v in initials.items()}
    # web-072：单字母候选（h→好/和/会）——jieba 单字条目按词频归到拼音首字母
    letters: dict[str, list[tuple[int, str]]] = {}
    with open(dt_path, encoding="utf-8") as f2:
        for line in f2:
            parts = line.split()
            if len(parts) < 3:
                continue
            word, freq = parts[0], int(parts[1])
            if len(word) != 1 or not ("一" <= word <= "鿿"):
                continue
            py = pinyin(word, style=Style.NORMAL)
            if not py or not py[0]:
                continue
            letter = py[0][0][0].lower()
            lb = letters.setdefault(letter, [])
            if word not in (w for _, w in lb):
                lb.append((freq, word))
    out_letters = {k: _rank(v)[:12] for k, v in letters.items()}
    payload = {"words": out, "initials": out_initials, "letters": out_letters}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    size_kb = OUT.stat().st_size // 1024
    print(f"keys={len(out)} initials={len(out_initials)} size={size_kb}KB -> {OUT}")
    for w in ["兔子", "乌龟", "嫦娥", "图书馆", "守株待兔"]:
        key = "".join(p[0] for p in pinyin(w, style=Style.NORMAL))
        assert w in out.get(key, []), f"{w} 未入库"
    assert out_initials.get("nh", [""])[0] == "你好", "首字母联想 你好 应居首（提权生效）"
    assert "年后" in out_initials.get("nh", []) and "您好" in out_initials.get("nh", [])
    h_letters = out_letters.get("h", [])
    assert all(c in h_letters for c in "好和会"), f"单字母 h 候选缺好/和/会: {h_letters}"
    print("coverage self-check OK")


if __name__ == "__main__":
    main()
