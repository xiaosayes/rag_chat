"""
项目配置管理模块
支持多项目独立配置，每个项目独占 Prompt、Qdrant 集合、BM25 索引
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.config import settings


# ========== 内置项目 Prompt 模板 ==========

# ----- 博物馆项目 -----
MUSEUM_PROMPTS = {
    "recommend": """你是一位专业的博物馆专家。请根据用户问题和提供的参考信息，给出优质的推荐。

## 推荐原则
1. 从参考信息中挑选 **3~5 个** 最具代表性的结果进行推荐
2. 每个推荐项需包含：**名称、朝代/时期、简要介绍、推荐理由**
3. 尽量覆盖 **不同类型**（文物、展览、主题活动等）
4. 推荐理由要具体，说明该结果为何值得关注
5. 如果参考信息不足，请如实说明，不要编造信息
6. 回答格式要清晰易读，有层次感

## 参考信息
{context}

## 输出格式要求
请使用结构化格式输出推荐结果。
7. 回答必须一次完成：**列出全部推荐项后直接结束**，不要重复推荐、不要追加与前面相同的推荐列表，不要在结尾再次生成新的推荐内容（bug-102 防循环）
""",
    "factual": """你是一位专业的博物馆专家。请根据用户问题和提供的参考信息，给出准确、详实的回答。

## 回答原则
1. 基于参考信息回答，不要编造事实
2. 如果参考信息不足，请如实说明
3. 引用参考信息时，可以提及来源名称
4. 回答要简洁明了

## 参考信息
{context}
""",
    "default": """你是一位专业的博物馆专家。请根据用户问题和提供的参考信息，给出专业、准确的回答。

## 回答原则
1. 基于参考信息回答
2. 如果参考信息不足，请如实说明
3. 回答要结构清晰、内容详实

## 参考信息
{context}
""",
    "chitchat": """你是一位友好的博物馆助手。

## 回答原则
1. 如果用户问的是博物馆相关的问题，请基于检索结果回答
2. 如果用户问的是闲聊（问候、天气、心情等），请友好回应
3. 如果用户问的是其他问题，请用你的通用知识回答
4. 回答要简洁、友好、有帮助
""",
}

# ----- 企业项目 -----
ENTERPRISE_PROMPTS = {
    "recommend": """你是一位专业的企业顾问。请根据用户问题和提供的参考信息，给出优质的推荐。

## 推荐原则
1. 从参考信息中挑选 **3~5 个** 最具代表性的结果进行推荐
2. 每个推荐项需包含：**名称、简介、推荐理由**
3. 尽量覆盖 **不同类别**（产品、方案、案例、文档等）
4. 推荐理由要具体，说明该结果为何值得关注
5. 如果参考信息不足，请如实说明，不要编造信息
6. 回答格式要清晰易读，有层次感

## 参考信息
{context}

## 输出格式要求
请使用结构化格式输出推荐结果。
7. 回答必须一次完成：**列出全部推荐项后直接结束**，不要重复推荐、不要追加与前面相同的推荐列表，不要在结尾再次生成新的推荐内容（bug-102 防循环）
""",
    "factual": """你是一位专业的企业顾问。请根据用户问题和提供的参考信息，给出准确、详实的回答。

## 回答原则
1. 基于参考信息回答，不要编造事实
2. 如果参考信息不足，请如实说明
3. 引用参考信息时，可以提及来源名称
4. 回答要简洁明了

## 参考信息
{context}
""",
    "default": """你是一位专业的企业顾问。请根据用户问题和提供的参考信息，给出专业、准确的回答。

## 回答原则
1. 基于参考信息回答
2. 如果参考信息不足，请如实说明
3. 回答要结构清晰、内容详实

## 参考信息
{context}
""",
    "chitchat": """你是一位友好的企业助手。

## 回答原则
1. 如果用户问的是企业相关的问题，请基于检索结果回答
2. 如果用户问的是闲聊（问候、天气、心情等），请友好回应
3. 如果用户问的是其他问题，请用你的通用知识回答
4. 回答要简洁、友好、有帮助
""",
}

# ========== 内置项目注册表 ==========

BUILTIN_PROJECTS = {
    "museum": {
        "id": "museum",
        "name": "博物馆知识库",
        "description": "文物、展览、参观须知等",
        "prompts": MUSEUM_PROMPTS,
        "collection_name": "project_museum",
    },
    "enterprise": {
        "id": "enterprise",
        "name": "企业知识库",
        "description": "企业介绍、产品方案、案例文档等",
        "prompts": ENTERPRISE_PROMPTS,
        "collection_name": "project_enterprise",
    },
}


# ========== 项目配置 ==========

class ProjectConfig:
    """单个项目的配置"""

    def __init__(self, project_id: str, config: Dict[str, Any]):
        self.id = project_id
        self.name = config.get("name", project_id)
        self.description = config.get("description", "")
        self.collection_name = config.get("collection_name", f"project_{project_id}")
        self.prompts = config.get("prompts", {})
        self.data_dir = settings.project_root / "data" / "raw" / project_id
        self.processed_dir = settings.project_root / "data" / "processed" / project_id

    def get_prompt(self, prompt_type: str, context: str = "") -> str:
        """获取指定类型的 Prompt 并填充上下文"""
        template = self.prompts.get(prompt_type, self.prompts.get("default", ""))
        # bug-056 修复：仅替换 {context} 占位符，避免模板中字面花括号
        # （如 JSON 示例 {"key": "value"}）触发 str.format() 的 KeyError/ValueError
        return template.replace("{context}", context)

    @property
    def chunk_cache_path(self) -> Path:
        """切片缓存文件路径"""
        return self.processed_dir / "chunks.json"

    @property
    def qdrant_path(self) -> Path:
        """Qdrant 数据库路径"""
        return self.processed_dir / "qdrant_db"


# ========== 项目管理器 ==========

class ProjectManager:
    """
    项目管理器
    负责加载、切换、管理多个项目的配置
    """

    def __init__(self, projects_dir: Optional[Path] = None):
        self.projects_dir = projects_dir or (settings.project_root / "data" / "projects")
        self._projects: Dict[str, ProjectConfig] = {}
        self._current_project_id: Optional[str] = None
        self._load_projects()

    def _load_projects(self):
        """加载所有项目配置（内置 + 外部 JSON）"""
        # 1. 加载内置项目
        for pid, cfg in BUILTIN_PROJECTS.items():
            self._projects[pid] = ProjectConfig(pid, cfg)
            logger.info(f"加载内置项目: {pid} - {cfg['name']}")

        # 2. 加载外部 JSON 项目配置
        if self.projects_dir.exists():
            for json_file in sorted(self.projects_dir.glob("*.json")):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    pid = cfg.get("id", json_file.stem)
                    if pid not in self._projects:
                        self._projects[pid] = ProjectConfig(pid, cfg)
                        logger.info(f"加载外部项目: {pid} - {cfg.get('name', pid)}")
                except Exception as e:
                    logger.warning(f"加载项目配置失败 {json_file.name}: {e}")

        logger.info(f"项目加载完成: 共 {len(self._projects)} 个项目")

    @property
    def current(self) -> Optional[ProjectConfig]:
        """当前项目配置"""
        if self._current_project_id is None:
            return None
        return self._projects.get(self._current_project_id)

    @property
    def current_id(self) -> Optional[str]:
        return self._current_project_id

    def switch_to(self, project_id: str) -> ProjectConfig:
        """切换到指定项目"""
        if project_id not in self._projects:
            raise ValueError(
                f"项目 '{project_id}' 不存在。可用项目: {list(self._projects.keys())}"
            )
        self._current_project_id = project_id
        logger.info(f"切换到项目: {project_id} - {self._projects[project_id].name}")
        return self._projects[project_id]

    def get_project(self, project_id: str) -> ProjectConfig:
        """获取项目配置（不切换）"""
        if project_id not in self._projects:
            raise ValueError(
                f"项目 '{project_id}' 不存在。可用项目: {list(self._projects.keys())}"
            )
        return self._projects[project_id]

    def list_projects(self) -> List[Dict[str, str]]:
        """列出所有项目"""
        return [
            {"id": p.id, "name": p.name, "description": p.description}
            for p in self._projects.values()
        ]

    def add_project(self, config: Dict[str, Any]) -> ProjectConfig:
        """动态添加项目"""
        pid = config.get("id", "")
        if not pid:
            raise ValueError("项目配置必须包含 'id' 字段")
        # bug-048 修复：校验项目 ID 格式，防止路径遍历（如 id="../evil"）
        # 将 JSON 文件写入项目目录之外
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
            raise ValueError(
                f"项目 ID 只能包含字母、数字、下划线、中划线: {pid!r}"
            )
        self._projects[pid] = ProjectConfig(pid, config)
        # 保存到 JSON 文件
        save_path = self.projects_dir / f"{pid}.json"
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info(f"已添加新项目: {pid}")
        return self._projects[pid]


# ========== 全局单例 ==========

project_manager = ProjectManager()