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


def format_recommendation(results: List[Dict[str, Any]]) -> str:
    """格式化推荐结果（用于 LLM 输出前的参考）"""
    lines = []
    for i, r in enumerate(results, 1):
        name = r.get("name", "未知")
        dynasty = r.get("dynasty", "未知")
        desc = truncate_text(r.get("description", "暂无描述"), 80)
        lines.append(f"[{i}] {name}（{dynasty}）：{desc}")
    return "\n".join(lines)