"""
工具函数模块
"""

import json
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger


class FatalAPIError(RuntimeError):
    """
    确定性 API 错误（4xx 非 429 限流），重试无意义，直接向调用方抛出。

    bug-095 修复：HTTP 400（参数非法/文本超长等）为客户端错误，
    重试无法解决且会浪费 API 调用与时间；抛出后由调用方展示服务端错误详情，
    便于定位根因（此前只记录状态码，服务端错误原因不可见）。
    """


def setup_logger(level: str = "INFO") -> None:
    """配置日志"""
    logger.remove()
    logger.add(
        sink=lambda msg: print(msg, end=""),
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>",
        colorize=True,
    )
    # 确保日志目录存在（loguru 不会自动创建父目录）
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        sink=str(log_dir / "rag_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
        encoding="utf-8",
    )


def generate_id(content: str) -> str:
    """根据内容生成唯一 ID

    使用完整 MD5（128 位）而非截断的 16 字符（64 位），
    避免 50000 条目时 ~50% 的冲突概率（bug-021）。
    """
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def load_json(path: Path) -> List[Dict[str, Any]]:
    """加载 JSON 文件"""
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info(f"已加载 {len(data)} 条数据 from {path}")
    return data


def save_json(data: Any, path: Path) -> None:
    """保存 JSON 文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"已保存 {len(data) if isinstance(data, list) else 'data'} 条数据 to {path}")


def truncate_text(text: str, max_length: int = 100) -> str:
    """截断文本用于显示"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


# emoji/表情/装饰图标正则（用于 LLM 输出过滤，bug-114）
# 覆盖范围（均为 Unicode 公开区，不误伤中文标点/字母/数字/普通符号）：
#   \U0001F000-\U0001FFFF  表情/交通/符号/扩展（含麻将、扑克、国旗、ZWJ 组合等）
#   \U00002600-\U000027BF  杂项符号 + 装饰符号/Dingbats（☀★❤✂✓➜ 等）
#   \U00002300-\U000023FF  杂项技术符号（⌚⏰⏳ 等）
#   \U000025A0-\U000025FF  几何形状（▫▪◾◽ 等小图标）
#   \U00002196-\U00002199  四角箭头（↖↗↘↙ emoji 风格；→↑←↓ 文本箭头保留）
#   \U00002B00-\U00002BFF  杂项符号和箭头（⭐⬆ 等）
#   \U0000FE00-\U0000FE0F  变体选择符（emoji 修饰符）
#   \U0000200D             零宽连接符（ZWJ）
#   \U00003030             波浪线符号（〰）
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F000-\U0001FFFF"
    "\U00002600-\U000027BF"
    "\U00002300-\U000023FF"
    "\U000025A0-\U000025FF"
    "\U00002196-\U00002199"
    "\U00002B00-\U00002BFF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "\U00003030"
    "]+"
)


def strip_emoji(text: str) -> str:
    """移除文本中的 emoji 表情与装饰图标（bug-114）

    用于 LLM 输出过滤：qwen 系列回答常带 emoji（😊🌟❤️ 等），
    统一移除使输出保持纯文本。不误伤中文标点、字母、数字与普通符号（如 © →）。
    """
    if not text:
        return text
    return EMOJI_PATTERN.sub("", text)


# ========== 答案文本清洗（TTS + 字幕展示，bug-115） ==========

# 行内代码内容判为命令/路径的命令关键字（命中则删除该内容）
_CODE_COMMAND_KEYWORDS = {
    "cd", "ls", "dir", "rm", "cp", "mv", "mkdir", "touch", "cat", "grep", "sed",
    "awk", "echo", "export", "source", "chmod", "chown", "ssh", "scp", "tar",
    "unzip", "zip", "make", "cmake", "java", "javac", "go", "cargo", "docker",
    "kubectl", "systemctl", "service", "kill", "ps", "top", "htop", "man", "which",
    "find", "xargs", "tee", "head", "tail", "wc", "sort", "gzip", "dd", "df", "du",
    "free", "uname", "pwd", "env", "alias", "history", "clear", "exit", "python",
    "python3", "pip", "pip3", "npm", "npx", "yarn", "node", "git", "curl", "wget",
    "apt", "apt-get", "conda", "install", "import", "from", "print", "sudo", "mount",
    "umount",
}

# 行内代码内容为已知文件扩展名（如 .py / .json）→ 判为路径/命令
_CODE_FILE_EXT_RE = re.compile(
    r"\.(py|pyc|sh|bat|cmd|ps1|json|yaml|yml|toml|ini|cfg|log|txt|md|exe|dll|so|"
    r"jar|war|zip|tar|gz|conf|db|sqlite|sql|env|html|css|js|ts|tsx|jsx|vue|go|rs|"
    r"c|cpp|h|java|class|xml|pdf|docx|xlsx|csv|lock|map)$",
    re.IGNORECASE,
)

# 句末标点集合（已以此结尾则不再补句号）
_SENTENCE_END_CHARS = "。！？!?…．.;；"

# 行尾可安全剥离的标点（剥离后再判断是否补句号）
_SENTENCE_STRIP_CHARS = "，、：:"

# 块级 HTML 标签（转为段落分隔 \n）
_BLOCK_HTML_TAGS = {
    "br", "p", "div", "li", "tr", "td", "th", "table", "ul", "ol", "h1", "h2",
    "h3", "h4", "h5", "h6", "section", "article", "header", "footer", "nav",
    "blockquote", "hr", "pre",
}


def _is_code_like_content(content: str) -> bool:
    """判断行内代码内容是否为命令/路径（是则删除，否则保留）"""
    s = content.strip()
    if not s:
        return True
    tokens = s.split()
    if tokens[0].lower() in _CODE_COMMAND_KEYWORDS:
        return True
    if len(tokens) > 1:
        return True
    if any(ch in s for ch in "/\\"):
        return True
    if re.match(r"^--?[A-Za-z]", s):
        return True
    if ".." in s or "=" in s:
        return True
    if _CODE_FILE_EXT_RE.search(s):
        return True
    return False


def _latex_to_speech(expr: str) -> Optional[str]:
    """简单 LaTeX 公式转口语描述；复杂/无意义返回 None（由调用方删除）"""
    e = expr.strip()
    if not e:
        return None
    # \frac{a}{b} → "b 分之 a"
    m = re.fullmatch(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", e)
    if m:
        return f"{m.group(2)} 分之 {m.group(1)}"
    # base^exp → "base 的平方/立方/次方"
    m = re.fullmatch(r"([A-Za-z0-9]+)\^\{?([0-9A-Za-z]+)\}?", e)
    if m:
        base, exp = m.group(1), m.group(2)
        if exp == "2":
            return f"{base} 的平方"
        if exp == "3":
            return f"{base} 的立方"
        return f"{base} 的 {exp} 次方"
    # base_sub → "base 下标 sub"
    m = re.fullmatch(r"([A-Za-z0-9]+)_\{?([0-9A-Za-z]+)\}?", e)
    if m:
        return f"{m.group(1)} 下标 {m.group(2)}"
    # a+b / a-b / a×b / a÷b → 口语
    m = re.fullmatch(r"([A-Za-z0-9]+)\s*([+\-×÷])\s*([A-Za-z0-9]+)", e)
    if m:
        ops = {"+": "加", "-": "减", "×": "乘", "÷": "除以"}
        return f"{m.group(1)} {ops[m.group(2)]} {m.group(3)}"
    return None


def _replace_latex_dollar(m: re.Match) -> str:
    """替换 $...$：货币（$ 后为数字）保留；简单公式转口语；复杂删除"""
    content = m.group(1).strip()
    if not content:
        return ""
    if content[0].isdigit():
        return m.group(0)  # $5 等为货币符号，保留原样
    speech = _latex_to_speech(content)
    return speech if speech is not None else ""


def _replace_latex_paren(m: re.Match) -> str:
    r"""替换 \(...\)：简单公式转口语；复杂删除"""
    speech = _latex_to_speech(m.group(1).strip())
    return speech if speech is not None else ""


def _strip_emphasis(text: str) -> str:
    """删除 Markdown 行内强调标记（粗体/斜体/删除线），保留中间文字"""
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"\*([^*\n]+?)\*", r"\1", text)
    # 单下划线强调：两侧需为非字母数字，避免误伤 model_name 等标识符
    text = re.sub(r"(?<![A-Za-z0-9])_([^_\n]+?)_(?![A-Za-z0-9])", r"\1", text)
    return text


def _convert_tilde_ranges(text: str) -> str:
    """数字区间波浪号（3~5 / 3～5）转 "到"，其余波浪号删除"""
    text = re.sub(
        r"(\d[0-9\-/年月日.]*)\s*[~～]\s*(\d[0-9\-/年月日.]*)",
        r" \1 到 \2 ",
        text,
    )
    return text.replace("~", "").replace("～", "")


def clean_text_for_tts(text: Optional[str]) -> str:
    """将答案原始文本清洗为适合语音合成（TTS）+ 字幕展示的纯文本（bug-115）

    规则：
      1. 删除 Markdown 语法符号（标题/粗体/斜体/删除线/引用/代码块/行内代码/分隔线/
         表格符号/链接语法），仅保留正文文字；行内代码内容为命令/路径时删除；
      2. 删除 HTML 标签及属性，保留标签内文字（块级标签转为段落分隔）；
      3. 删除 LaTeX 公式（简单公式转口语描述，如 $x^2$ → "x 的平方"；复杂/无意义删除）、
         控制字符、零宽字符、制表符、emoji；
      4. 保留中文/英文标点、数字、%、货币符号（¥/$/°C）、版本号、商标符号等正常字符；
      5. 数字区间波浪号（3~5）转 "到"；连续空格压缩为单个；段落间最多一个空行；
         每句结尾补标点（标题行除外）。
    """
    if not text:
        return ""

    result = text.replace("\r\n", "\n").replace("\r", "\n")

    # 1. 删除代码块（整块删除）
    result = re.sub(r"```.*?```", "", result, flags=re.DOTALL)

    # 2. 删除 HTML：script/style 整体删除；块级标签转换行；其余标签删除保留内文
    result = re.sub(r"<script[^>]*>.*?</script>", "", result, flags=re.DOTALL | re.IGNORECASE)
    result = re.sub(r"<style[^>]*>.*?</style>", "", result, flags=re.DOTALL | re.IGNORECASE)

    def _html_repl(m: re.Match) -> str:
        tag = m.group(1).lower()
        return "\n" if tag in _BLOCK_HTML_TAGS else ""

    result = re.sub(r"</?([a-zA-Z][a-zA-Z0-9]*)(?:\s[^>]*)?/?>", _html_repl, result)

    # 3. 删除 LaTeX 公式
    result = re.sub(r"\$([^$\n]+?)\$", _replace_latex_dollar, result)
    result = re.sub(r"\\\(([^\\\n]+?)\\\)", _replace_latex_paren, result)

    # 4. Markdown 链接/图片语法 → 保留文字
    result = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", result)
    result = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", result)

    # 5. 行内代码：内容为命令/路径则删除，否则保留文字
    result = re.sub(
        r"`([^`\n]+)`",
        lambda m: "" if _is_code_like_content(m.group(1)) else m.group(1),
        result,
    )

    # 6. 逐行处理行级 Markdown 语法（表格按块处理，避免分隔行遗留空行）
    lines = result.split("\n")
    cleaned = []  # (line, is_heading)
    i = 0
    while i < len(lines):
        line = lines[i]
        # 表格块：连续以 | 开头的行（含分隔行）合并处理，行间不留空行
        if line.lstrip().startswith("|"):
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                row = lines[i]
                if not re.fullmatch(r"\s*\|?[\s|:]+-{3,}[\s|:-]*\|?\s*", row):
                    cells = [c.strip() for c in row.strip().strip("|").split("|")]
                    cleaned.append(("，".join(cells), False))
                i += 1
            continue
        is_heading = bool(re.match(r"^\s*#{1,6}\s", line))
        line = re.sub(r"^\s*#{1,6}\s*", "", line)          # 标题标记
        line = re.sub(r"^\s*>\s?", "", line)                # 引用标记
        if re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            line = ""                                          # 分隔线
        line = re.sub(r"^\s*[-*+]\s+", "", line)           # 无序列表标记
        cleaned.append((line, is_heading))
        i += 1

    # 7~10. 逐行规范化
    final_lines = []
    for line, is_heading in cleaned:
        line = _strip_emphasis(line)
        line = _convert_tilde_ranges(line)
        line = strip_emoji(line)
        # 控制字符 / 零宽字符 / 制表符（→ 空格）
        line = re.sub(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2028\u2029\u2060\ufeff]",
            "",
            line,
        )
        line = line.replace("\t", " ")
        line = re.sub(r" {2,}", " ", line)                  # 连续空格压缩
        line = re.sub(r"^(\d{1,2})\.(?=[^\d\s])", r"\1. ", line)  # 列表序号补空格
        if not is_heading:                                    # 句末标点（标题行除外）
            stripped = line.rstrip(_SENTENCE_STRIP_CHARS + " ")
            if stripped and not stripped.endswith(tuple(_SENTENCE_END_CHARS)):
                stripped += "。"
            line = stripped
        final_lines.append((line, is_heading))

    # 11. 合并：标题行后的空行剔除（Markdown 排版记号）；段落间最多一个空行
    merged = []
    for idx, (line, is_heading) in enumerate(final_lines):
        if line == "" and idx > 0 and final_lines[idx - 1][1]:
            continue
        merged.append(line)
    result = "\n".join(merged)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = "\n".join(l.strip() for l in result.split("\n"))
    return result.strip()


def format_recommendation(results: List[Dict[str, Any]]) -> str:
    """格式化推荐结果（用于 LLM 输出前的参考）"""
    lines = []
    for i, r in enumerate(results, 1):
        name = r.get("name", "未知")
        dynasty = r.get("dynasty", "未知")
        desc = truncate_text(r.get("description", "暂无描述"), 80)
        lines.append(f"[{i}] {name}（{dynasty}）：{desc}")
    return "\n".join(lines)