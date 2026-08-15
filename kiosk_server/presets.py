"""预设问题池（web-002）：服务器可编辑 JSON（gitignored）+ 缺省池兜底。

前端 `GET /api/presets` 拿全量池后随机抽 8 条展示，「换一批」= 前端重抽（零额外请求）。
上线前按实际知识库内容改 `data/kiosk/preset_questions.json` 即生效，无需重发前端。
"""
from __future__ import annotations

import json
import logging
from typing import List

logger = logging.getLogger(__name__)

# 缺省池：设计稿 8 条 + 同风格补 8 条（湘少图场景，用户拍板「给几个随机问题作为预设」）
DEFAULT_PRESETS: List[str] = [
    "志愿者报名条件",
    "志愿者的工作内容",
    "如何办证，办证须知",
    "图书丢失、污损怎么办？",
    "有什么不能带的东西吗？",
    "楼层介绍",
    "湖南省少年儿童图书馆的简介",
    "湖南省少年儿童图书馆开放时间",
    "借书证怎么办理？",
    "一次可以借几本书？",
    "借书期限是多久？",
    "逾期还书会怎么样？",
    "图书馆里有无线网络吗？",
    "自习室怎么预约？",
    "周末开门吗？",
    "儿童阅览室在几楼？",
]

_MAX_PRESETS = 64


def load_presets(path: str) -> List[str]:
    """读预设 JSON（{"questions":[...]} 或裸列表），任何异常回退缺省池。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("questions") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("预设必须是列表或含 questions 列表的对象")
        cleaned: List[str] = []
        for it in items:
            if isinstance(it, str):
                s = it.strip()
                if s and s not in cleaned:
                    cleaned.append(s)
        if not cleaned:
            raise ValueError("预设问题为空")
        return cleaned[:_MAX_PRESETS]
    except FileNotFoundError:
        logger.info("预设文件不存在，用缺省池: %s", path)
        return list(DEFAULT_PRESETS)
    except Exception as e:  # JSON 错/类型错/空池 → 兜底，绝不让端点 500
        logger.warning("预设文件无效（%s），用缺省池: %s", e, path)
        return list(DEFAULT_PRESETS)
