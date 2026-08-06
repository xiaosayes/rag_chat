"""
工具函数模块
"""

import json
import hashlib
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


def format_recommendation(results: List[Dict[str, Any]]) -> str:
    """格式化推荐结果（用于 LLM 输出前的参考）"""
    lines = []
    for i, r in enumerate(results, 1):
        name = r.get("name", "未知")
        dynasty = r.get("dynasty", "未知")
        desc = truncate_text(r.get("description", "暂无描述"), 80)
        lines.append(f"[{i}] {name}（{dynasty}）：{desc}")
    return "\n".join(lines)