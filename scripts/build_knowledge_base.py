"""
知识库构建脚本 v2.0
支持从多种数据源构建知识库：
  1. JSON/CSV 文物数据（artifacts.json）
  2. 多格式文档目录（PDF、Word、TXT、图片等）
  3. 混合模式（JSON + 文档目录合并）
"""

import sys
import argparse
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from src.config import settings
from src.utils import setup_logger
from src.rag_pipeline import RAGPipeline
from src.document_loader import DocumentLoader
from src.data_loader import DataLoader


def build_from_json(pipeline: RAGPipeline, data_path: Path, overwrite: bool) -> dict:
    """从 JSON 文物数据构建知识库"""
    if not data_path.exists():
        logger.error(f"数据文件不存在: {data_path}")
        logger.info("提示: 可先运行 python scripts/generate_mock_data.py 生成测试数据")
        sys.exit(1)

    stats = pipeline.build_knowledge_base(
        data_path=data_path,
        overwrite=overwrite,
    )
    return stats


def build_from_documents(
    pipeline: RAGPipeline,
    source_path: Path,
    category: str,
    overwrite: bool,
    recursive: bool,
    enable_ocr: bool,
    ocr_engine: str,
) -> dict:
    """从多格式文档构建知识库"""
    if not source_path.exists():
        logger.error(f"文档路径不存在: {source_path}")
        sys.exit(1)

    stats = pipeline.build_knowledge_base_from_documents(
        source_path=source_path,
        category=category,
        overwrite=overwrite,
        recursive=recursive,
        enable_ocr=enable_ocr,
        ocr_engine=ocr_engine,
    )
    return stats


def build_mixed(
    pipeline: RAGPipeline,
    json_path: Path,
    doc_path: Path,
    overwrite: bool,
    recursive: bool,
    enable_ocr: bool,
    ocr_engine: str,
) -> dict:
    """混合模式：先加载 JSON 文物数据，再加载文档"""
    if json_path.exists():
        artifacts = DataLoader.load(json_path)
        logger.info(f"从 JSON 加载了 {len(artifacts)} 件文物")
    else:
        artifacts = []
        logger.warning(f"JSON 文件不存在，跳过: {json_path}")

    # 2. 加载文档
    if doc_path.exists():
        doc_loader = DocumentLoader(enable_ocr=enable_ocr, ocr_engine=ocr_engine)
        doc_artifacts = doc_loader.load_all_as_artifacts(
            source=doc_path,
            category="文档资料",
            recursive=recursive,
        )
        logger.info(f"从文档加载了 {len(doc_artifacts)} 件")
        artifacts.extend(doc_artifacts)
    else:
        logger.warning(f"文档路径不存在，跳过: {doc_path}")

    if not artifacts:
        logger.error("没有找到任何数据源！")
        sys.exit(1)

    # 3. 统一构建知识库
    stats = pipeline.build_knowledge_base(
        artifacts=artifacts,
        overwrite=overwrite,
    )
    return stats


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="知识库构建工具 v3.0 - 支持多项目",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 构建博物馆项目知识库
  python scripts/generate_mock_project_data.py --project museum
  python scripts/build_knowledge_base.py --project museum --source json

  # 构建企业项目知识库
  python scripts/generate_mock_project_data.py --project enterprise
  python scripts/build_knowledge_base.py --project enterprise --source json

  # 从多格式文档目录构建（指定项目）
  python scripts/build_knowledge_base.py --project museum --source docs --doc-path ./data/raw/museum/docs

  # 混合模式
  python scripts/build_knowledge_base.py --project enterprise --source mixed

  # 列出可用项目
  python scripts/build_knowledge_base.py --list-projects
        """,
    )
    parser.add_argument(
        "--project", type=str, default=None,
        help="项目 ID（museum / enterprise / 自定义）",
    )
    parser.add_argument(
        "--list-projects", action="store_true",
        help="列出所有可用项目",
    )
    parser.add_argument(
        "--source", type=str, default="json",
        choices=["json", "docs", "mixed"],
        help="数据源类型: json, docs, mixed",
    )
    parser.add_argument(
        "--json-path", type=str, default=None,
        help="JSON 数据文件路径（默认根据项目 ID 自动查找）",
    )
    parser.add_argument(
        "--doc-path", type=str, default=None,
        help="文档目录路径",
    )
    parser.add_argument(
        "--category", type=str, default="文档资料",
        help="文档分类标签",
    )
    parser.add_argument(
        "--no-ocr", action="store_true",
        help="禁用图片 OCR",
    )
    parser.add_argument(
        "--ocr-engine", type=str, default="paddle",
        choices=["paddle", "tesseract"],
        help="OCR 引擎",
    )
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="不递归扫描子目录",
    )
    parser.add_argument(
        "--no-overwrite", action="store_true",
        help="不覆盖已有知识库",
    )

    args = parser.parse_args()
    setup_logger(settings.log_level)

    # 如果只是列出项目
    if args.list_projects:
        from src.project import project_manager
        print("\n📋 可用项目:")
        print("=" * 50)
        for p in project_manager.list_projects():
            print(f"  {p['id']:20s}  {p['name']}")
            if p['description']:
                print(f"  {'':20s}  {p['description']}")
            print()
        return

    if not args.project:
        logger.error("请指定项目 ID: --project museum 或 --project enterprise")
        logger.info("可用项目: python scripts/build_knowledge_base.py --list-projects")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info(f"知识库构建工具 v3.0 - 项目: {args.project}")
    logger.info(f"数据源模式: {args.source}")
    logger.info("=" * 60)

    # 检查 API Key
    try:
        settings.validate_api_key()
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    # 初始化 RAG 流水线（指定项目）
    pipeline = RAGPipeline(
        local_mode=True,
        memory_mode=settings.qdrant_memory_mode,
        project_id=args.project,
    )

    # 确定路径（优先使用项目专属路径）
    if args.project and not args.json_path:
        from src.project import project_manager
        proj_cfg = project_manager.get_project(args.project)
        json_path = proj_cfg.data_dir / "data.json"
        if not json_path.exists():
            json_path = proj_cfg.data_dir / "artifacts.json"
        doc_path = proj_cfg.data_dir / "docs"
    else:
        json_path = Path(args.json_path) if args.json_path else (
            settings.raw_data_path / "artifacts.json"
        )
        doc_path = Path(args.doc_path) if args.doc_path else (
            settings.raw_data_path / "docs"
        )

    overwrite = not args.no_overwrite
    recursive = not args.no_recursive
    enable_ocr = not args.no_ocr

    # 根据模式构建
    if args.source == "json":
        stats = build_from_json(pipeline, json_path, overwrite)
    elif args.source == "docs":
        stats = build_from_documents(
            pipeline, doc_path, args.category,
            overwrite, recursive, enable_ocr, args.ocr_engine,
        )
    elif args.source == "mixed":
        stats = build_mixed(
            pipeline, json_path, doc_path,
            overwrite, recursive, enable_ocr, args.ocr_engine,
        )
    else:
        logger.error(f"未知的数据源模式: {args.source}")
        sys.exit(1)

    # 输出统计信息
    logger.info("=" * 60)
    logger.info("知识库构建完成！")
    for k, v in stats.items():
        logger.info(f"  - {k}: {v}")
    logger.info("=" * 60)
    # 使用实际路径（项目专属路径优先）
    if pipeline.project_cfg:
        vec_path = pipeline.vector_store.local_path
        cache_path = pipeline.project_cfg.chunk_cache_path
    else:
        vec_path = settings.processed_data_path / "qdrant_db"
        cache_path = settings.processed_data_path / "chunks.json"
    logger.info("数据存储位置:")
    logger.info(f"  - 向量数据库: {vec_path}")
    logger.info(f"  - 切片缓存: {cache_path}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()