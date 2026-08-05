"""
多格式测试文档生成器
生成各种格式的示例文档，用于测试多格式文档加载功能
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger


def create_directory_structure(base_dir: Path):
    """创建测试文档目录结构"""
    dirs = [
        base_dir / "txt",
        base_dir / "md",
        base_dir / "images",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def create_sample_txt(dir_path: Path):
    """创建示例 TXT 文件"""
    files = {
        "青铜器概述.txt": """中国古代青铜器概述
========================

青铜器是中国古代文明的重要标志之一。中国青铜时代始于夏代，盛于商周，
延续至秦汉。青铜器种类繁多，包括礼器、兵器、车马器、工具等。

一、青铜礼器
鼎是青铜礼器中最重要的一种，用于祭祀、宴飨等场合。
著名的青铜鼎有：司母戊鼎、大盂鼎、毛公鼎等。

二、青铜兵器
越王勾践剑是春秋时期青铜兵器的代表，历经两千多年依然锋利不锈。

三、青铜乐器
曾侯乙编钟是战国时期的大型青铜乐器，全套65件，音律准确。
""",
        "瓷器发展简史.txt": """中国瓷器发展简史
================

中国是瓷器的故乡，瓷器的发展经历了漫长的过程。

一、原始瓷器（商周时期）
原始青瓷是最早的瓷器形式。

二、青瓷时代（东汉-隋唐）
越窑青瓷代表了唐代青瓷的最高水平。

三、彩瓷时代（宋元明清）
- 宋代：汝窑、官窑、哥窑、钧窑、定窑五大名窑
- 元代：元青花瓷达到顶峰
- 明代：成化斗彩、永乐甜白
- 清代：珐琅彩、粉彩
""",
        "书画艺术.txt": """中国古代书画艺术
================

中国书画艺术源远流长，是中华文化的重要组成部分。

十大传世名画：
1. 东晋·顾恺之《洛神赋图》
2. 唐代·阎立本《步辇图》
3. 唐代·张萱、周昉《唐宫仕女图》
4. 唐代·韩滉《五牛图》
5. 五代·顾闳中《韩熙载夜宴图》
6. 北宋·王希孟《千里江山图》
7. 北宋·张择端《清明上河图》
8. 元代·黄公望《富春山居图》
9. 明代·仇英《汉宫春晓图》
10. 清代·郎世宁《百骏图》
""",
    }

    for filename, content in files.items():
        filepath = dir_path / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content.strip())
        logger.info(f"创建: {filepath}")


def create_sample_md(dir_path: Path):
    """创建示例 Markdown 文件"""
    files = {
        "玉器文化.md": """# 中国玉器文化

## 玉器起源
中国玉器文化源远流长，最早可追溯到 **新石器时代**。

### 红山文化玉器
- **玉猪龙**：红山文化代表性玉器，C形龙造型
- **玉勾云形器**：造型独特，工艺精湛

### 良渚文化玉器
- **玉琮**：外方内圆，是祭祀天地的礼器
- **玉璧**：圆形，中间有孔，象征天

## 汉代玉器
汉代玉器工艺达到高峰，**金缕玉衣** 是汉代丧葬玉器的代表。

> 金缕玉衣使用玉片2498片，金丝约1100克。

## 清代玉器
清代玉雕工艺登峰造极，**大禹治水图玉山** 重达5300公斤，
是中国最大的玉雕作品。
""",
        "丝绸之路文物.md": """# 丝绸之路上的文物

## 概述
丝绸之路是古代连接中国与中亚、欧洲的贸易通道，
促进了东西方文化交流。

## 代表性文物

### 1. 唐三彩骆驼载乐俑
- **年代**：唐代
- **材质**：陶器
- **现藏**：中国国家博物馆
- **意义**：反映了唐代丝绸之路的繁荣

### 2. 鎏金舞马衔杯银壶
- **年代**：唐代
- **材质**：金银器
- **现藏**：陕西省历史博物馆
- **意义**：印证了唐代宫廷舞马表演

## 文化交流
丝绸之路不仅传播了商品，也促进了宗教、艺术、技术的交流。
佛教沿丝绸之路传入中国，并逐渐中国化。
""",
        "文物修复与保护.md": """# 文物修复与保护

## 基本原则
1. **修旧如旧**：保持文物的历史原貌
2. **可逆性**：修复材料应可去除
3. **可识别性**：修复部分应与原物有所区别

## 常见修复技术

### 青铜器修复
- 去锈处理
- 整形修复
- 补配缺失部分

### 陶瓷修复
- 粘接
- 补缺
- 作色

### 书画修复
- 揭裱
- 补绢
- 全色

## 现代科技在文物保护中的应用
- X射线探伤
- 3D扫描与打印
- 红外成像分析
- 数字化保护
""",
    }

    for filename, content in files.items():
        filepath = dir_path / filename
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content.strip())
        logger.info(f"创建: {filepath}")


def create_sample_images(dir_path: Path):
    """创建图片占位说明文件（实际图片需要在现场准备）"""
    readme_content = """# 图片文档目录

请将需要 OCR 识别的文物图片放入此目录，支持的格式：
- PNG (.png)
- JPEG (.jpg, .jpeg)
- GIF (.gif)
- WebP (.webp)
- BMP (.bmp)
- TIFF (.tiff, .tif)

## 建议的测试图片
1. 文物照片（青铜器、瓷器、书画等）
2. 博物馆展品说明牌
3. 文物拓片
4. 古籍书页

## OCR 引擎选择
- PaddleOCR（推荐）: GPU 加速，中文识别效果最佳
  pip install paddlepaddle-gpu paddleocr

- Tesseract OCR（备选）:
  Linux: sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim
  Windows: 下载安装 https://github.com/UB-Mannheim/tesseract/wiki
"""
    readme_path = dir_path / "README.md"
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content.strip())
    logger.info(f"创建: {readme_path}")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="生成多格式测试文档")
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="输出目录（默认: data/raw/docs）",
    )
    args = parser.parse_args()

    # 确定输出目录
    if args.output_dir:
        base_dir = Path(args.output_dir)
    else:
        base_dir = Path(__file__).resolve().parent.parent / "data" / "raw" / "docs"

    # 创建目录结构
    dirs = create_directory_structure(base_dir)

    # 创建各类文档
    create_sample_txt(dirs[0])
    create_sample_md(dirs[1])
    create_sample_images(dirs[2])

    print(f"\n{'='*60}")
    print("📄 多格式测试文档生成完成！")
    print(f"{'='*60}")
    print(f"\n📁 文档目录: {base_dir}")
    print(f"\n  生成的文件:")
    for d in dirs:
        files = list(d.glob("*"))
        for f in files:
            print(f"    📄 {f.relative_to(base_dir.parent.parent)}")
    print(f"\n💡 提示: 现在可以运行以下命令构建知识库:")
    print(f"  python scripts/build_knowledge_base.py --source docs --doc-path {base_dir}")
    print(f"  python scripts/build_knowledge_base.py --source mixed --doc-path {base_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()