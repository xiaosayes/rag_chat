"""
多项目 Mock 数据生成器
为 museum 和 enterprise 两个项目生成测试数据
"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loguru import logger


# ========== 博物馆项目数据（15件文物 + 展览信息 + 参观须知） ==========

MUSEUM_ARTIFACTS = [
    {
        "name": "司母戊鼎（后母戊鼎）",
        "dynasty": "商代晚期", "category": "青铜器",
        "location": "中国国家博物馆",
        "description": "目前已知中国古代最重的青铜器，重832.84公斤，高133厘米。鼎腹内壁铸有'后母戊'三字。",
        "historical_significance": "代表了商代青铜铸造技术的巅峰，是中华文明的重要象征。",
        "tags": ["国宝", "青铜器", "商代", "镇馆之宝"],
        "importance": 5,
    },
    {
        "name": "清明上河图",
        "dynasty": "北宋", "category": "书画",
        "location": "故宫博物院",
        "description": "北宋画家张择端创作的风俗画长卷，宽24.8厘米，长528.7厘米。记录北宋都城汴京的城市面貌。",
        "historical_significance": "中国风俗画的巅峰之作，'中国十大传世名画'之一。",
        "tags": ["国宝", "书画", "北宋", "十大传世名画"],
        "importance": 5,
    },
    {
        "name": "元青花萧何月下追韩信图梅瓶",
        "dynasty": "元代", "category": "瓷器",
        "location": "南京市博物馆",
        "description": "高44.1厘米，小口、短颈、丰肩、瘦底。瓶身绘有'萧何月下追韩信'历史故事图案。",
        "historical_significance": "元青花瓷中的极品，存世仅此一件，极为珍贵。",
        "tags": ["国宝", "瓷器", "元代", "元青花"],
        "importance": 5,
    },
    {
        "name": "马踏飞燕（铜奔马）",
        "dynasty": "东汉", "category": "青铜器",
        "location": "甘肃省博物馆",
        "description": "高34.5厘米，长45厘米。造型为骏马三足腾空、一足踏飞燕。",
        "historical_significance": "中国古代雕塑艺术的杰出代表，中国旅游标志。",
        "tags": ["国宝", "青铜器", "东汉", "旅游标志"],
        "importance": 5,
    },
    {
        "name": "曾侯乙编钟",
        "dynasty": "战国", "category": "青铜器",
        "location": "湖北省博物馆",
        "description": "全套编钟共65件，分三层八组悬挂，音域跨五个半八度。",
        "historical_significance": "世界上数量最多、保存最完整的青铜编钟。",
        "tags": ["国宝", "青铜器", "战国", "乐器"],
        "importance": 5,
    },
    {
        "name": "越王勾践剑",
        "dynasty": "春秋", "category": "青铜器",
        "location": "湖北省博物馆",
        "description": "通长55.7厘米，剑身布满黑色菱形暗纹，出土时毫无锈蚀，依然锋利。",
        "historical_significance": "历经两千多年依然锋利不锈，代表了春秋时期越国青铜铸造技术的最高水平。",
        "tags": ["国宝", "青铜器", "春秋", "兵器"],
        "importance": 5,
    },
    {
        "name": "翠玉白菜",
        "dynasty": "清代", "category": "玉器",
        "location": "台北故宫博物院",
        "description": "长18.7厘米，由一块半白半绿的翠玉雕琢而成，菜叶上雕有螽斯和蝗虫。",
        "historical_significance": "俏色巧雕工艺的巅峰之作，台北故宫博物院三大镇馆之宝之一。",
        "tags": ["国宝", "玉器", "清代", "台北故宫博物院"],
        "importance": 5,
    },
    {
        "name": "秦陵兵马俑",
        "dynasty": "秦代", "category": "陶器",
        "location": "秦始皇兵马俑博物馆",
        "description": "已出土陶俑、陶马约8000件，包括步兵俑、骑兵俑、将军俑等。每个陶俑面部表情各不相同。",
        "historical_significance": "被誉为'世界第八大奇迹'，1987年被列入世界文化遗产。",
        "tags": ["国宝", "陶器", "秦代", "兵马俑", "世界文化遗产"],
        "importance": 5,
    },
    {
        "name": "汝窑天青釉弦纹樽",
        "dynasty": "宋代", "category": "瓷器",
        "location": "故宫博物院",
        "description": "高12.9厘米，通体施天青釉，釉色青中泛蓝，有细密开片纹。",
        "historical_significance": "汝窑为宋代五大名窑之首，传世作品不足百件。",
        "tags": ["国宝", "瓷器", "宋代", "汝窑", "五大名窑"],
        "importance": 5,
    },
    {
        "name": "金缕玉衣",
        "dynasty": "西汉", "category": "玉器",
        "location": "河北省博物院",
        "description": "长1.88米，共用玉片2498片，金丝约1100克。玉片之间用金丝编缀。",
        "historical_significance": "中国考古史上首次发现的金缕玉衣，反映汉代丧葬制度。",
        "tags": ["国宝", "玉器", "西汉", "金缕玉衣"],
        "importance": 5,
    },
]

MUSEUM_EXHIBITIONS = [
    {
        "name": "古代中国基本陈列",
        "dynasty": "通史陈列", "category": "展览",
        "location": "中国国家博物馆",
        "description": "以古代中国历史发展为主线，展出文物2000余件，涵盖从远古到明清的各个历史时期。",
        "tags": ["展览", "中国国家博物馆", "通史"],
        "importance": 4,
    },
    {
        "name": "千里江山——历代青绿山水画展",
        "dynasty": "历代", "category": "展览",
        "location": "故宫博物院",
        "description": "展出从唐代至清代的青绿山水画作50余件，包括王希孟《千里江山图》等传世名作。",
        "tags": ["展览", "故宫博物院", "书画"],
        "importance": 4,
    },
]

MUSEUM_VISITOR_INFO = [
    {
        "name": "参观须知",
        "dynasty": "", "category": "服务信息",
        "location": "各博物馆",
        "description": "参观博物馆请提前预约，携带身份证件。禁止携带易燃易爆物品，展厅内禁止饮食、吸烟。请勿触摸展品，拍照时请关闭闪光灯。",
        "tags": ["参观", "须知", "服务"],
        "importance": 3,
    },
    {
        "name": "开放时间",
        "dynasty": "", "category": "服务信息",
        "location": "各博物馆",
        "description": "大多数博物馆周二至周日开放，周一闭馆（法定节假日除外）。夏季（4月-10月）9:00-17:00，冬季（11月-3月）9:00-16:30。",
        "tags": ["开放时间", "服务"],
        "importance": 3,
    },
]


# ========== 企业项目数据 ==========

ENTERPRISE_COMPANY = [
    {
        "name": "星辰科技有限公司",
        "dynasty": "", "category": "企业概况",
        "location": "深圳市南山区科技园",
        "description": "星辰科技成立于2015年，是一家专注于人工智能与大数据解决方案的国家高新技术企业。公司现有员工500余人，其中研发人员占比超过60%。",
        "tags": ["企业", "科技", "AI", "大数据"],
        "importance": 5,
    },
    {
        "name": "发展历程",
        "dynasty": "", "category": "企业概况",
        "location": "",
        "description": "2015年公司成立，获得天使轮融资；2017年推出首款AI产品，获国家高新技术企业认定；2019年完成B轮融资，营收突破1亿；2021年入选国家级专精特新'小巨人'企业；2023年营收突破5亿，员工规模达500人。",
        "tags": ["企业", "发展", "历程"],
        "importance": 4,
    },
    {
        "name": "企业文化",
        "dynasty": "", "category": "企业概况",
        "location": "",
        "description": "使命：用科技让世界更智能。愿景：成为全球领先的AI解决方案提供商。价值观：客户第一、创新驱动、诚信务实、合作共赢。",
        "tags": ["企业", "文化", "使命", "愿景"],
        "importance": 4,
    },
]

ENTERPRISE_PRODUCTS = [
    {
        "name": "星云AI平台",
        "dynasty": "", "category": "产品方案",
        "location": "",
        "description": "一站式AI开发平台，支持从数据标注、模型训练到部署上线的全流程管理。内置50+预训练模型，支持计算机视觉、自然语言处理、语音识别等主流AI任务。",
        "tags": ["产品", "AI", "平台", "机器学习"],
        "importance": 5,
    },
    {
        "name": "星辰智能客服系统",
        "dynasty": "", "category": "产品方案",
        "location": "",
        "description": "基于大语言模型的智能客服解决方案，支持多渠道接入（网页、APP、微信、电话），7x24小时自动应答，意图识别准确率达95%以上。",
        "tags": ["产品", "客服", "AI", "大模型"],
        "importance": 5,
    },
    {
        "name": "数据星河大数据平台",
        "dynasty": "", "category": "产品方案",
        "location": "",
        "description": "企业级大数据管理与分析平台，支持PB级数据存储与实时计算。提供数据采集、清洗、存储、分析、可视化一站式解决方案。",
        "tags": ["产品", "大数据", "平台", "数据分析"],
        "importance": 4,
    },
    {
        "name": "星盾网络安全系统",
        "dynasty": "", "category": "产品方案",
        "location": "",
        "description": "基于AI的网络安全防护系统，支持入侵检测、流量分析、威胁情报、漏洞扫描等功能。已通过国家信息安全等级保护三级认证。",
        "tags": ["产品", "安全", "网络", "AI"],
        "importance": 4,
    },
]

ENTERPRISE_CASES = [
    {
        "name": "某大型银行智能客服项目",
        "dynasty": "", "category": "案例",
        "location": "上海",
        "description": "为某国有大型银行部署智能客服系统，覆盖2000+网点，日均处理咨询量50万+，客户满意度提升30%，人工成本降低40%。",
        "tags": ["案例", "银行", "客服", "金融"],
        "importance": 5,
    },
    {
        "name": "某电商平台推荐系统",
        "dynasty": "", "category": "案例",
        "location": "杭州",
        "description": "为头部电商平台构建AI推荐系统，支持亿级用户个性化推荐，CTR提升25%，GMV增长18%。系统峰值TPS达10万+。",
        "tags": ["案例", "电商", "推荐", "AI"],
        "importance": 5,
    },
    {
        "name": "某制造企业数字化转型",
        "dynasty": "", "category": "案例",
        "location": "苏州",
        "description": "为某大型制造企业提供数字化转型整体方案，包括智能质检、预测性维护、生产排程优化等模块，良品率提升15%，设备停机时间减少40%。",
        "tags": ["案例", "制造", "数字化", "工业"],
        "importance": 4,
    },
]

ENTERPRISE_DOCS = [
    {
        "name": "员工入职指南",
        "dynasty": "", "category": "文档",
        "location": "",
        "description": "欢迎加入星辰科技！入职流程包括：1) 签署劳动合同 2) 领取办公设备 3) IT账号开通 4) 部门入职培训 5) 导师分配。试用期3个月。",
        "tags": ["文档", "入职", "HR"],
        "importance": 3,
    },
    {
        "name": "差旅报销制度",
        "dynasty": "", "category": "文档",
        "location": "",
        "description": "出差标准：一线城市住宿500元/天，餐补150元/天；二线城市住宿350元/天，餐补100元/天。交通费实报实销。需提前提交出差申请单。",
        "tags": ["文档", "差旅", "报销", "制度"],
        "importance": 3,
    },
]


def generate_museum_data() -> List[Dict[str, Any]]:
    """生成博物馆项目数据"""
    data = []
    for item in MUSEUM_ARTIFACTS + MUSEUM_EXHIBITIONS + MUSEUM_VISITOR_INFO:
        artifact = {
            "name": item["name"],
            "dynasty": item.get("dynasty", ""),
            "category": item.get("category", "文物"),
            "material": item.get("material", ""),
            "location": item.get("location", ""),
            "description": item.get("description", ""),
            "historical_significance": item.get("historical_significance", ""),
            "cultural_value": item.get("cultural_value", ""),
            "tags": item.get("tags", []),
            "importance": item.get("importance", 3),
        }
        data.append(artifact)
    return data


def generate_enterprise_data() -> List[Dict[str, Any]]:
    """生成企业项目数据"""
    data = []
    for item in ENTERPRISE_COMPANY + ENTERPRISE_PRODUCTS + ENTERPRISE_CASES + ENTERPRISE_DOCS:
        artifact = {
            "name": item["name"],
            "dynasty": "",
            "category": item.get("category", "文档"),
            "material": "",
            "location": item.get("location", ""),
            "description": item.get("description", ""),
            "historical_significance": item.get("historical_significance", ""),
            "cultural_value": item.get("cultural_value", ""),
            "tags": item.get("tags", []),
            "importance": item.get("importance", 3),
        }
        data.append(artifact)
    return data


def save_data(data: List[Dict[str, Any]], output_path: Path):
    """保存数据到 JSON 文件"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"已保存 {len(data)} 条数据到: {output_path}")


def print_statistics(project: str, data: List[Dict[str, Any]]):
    """打印统计信息"""
    from collections import Counter
    categories = Counter(item.get("category", "未分类") for item in data)
    print(f"\n📊 {project} 数据统计 ({len(data)} 条)")
    print("-" * 40)
    for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
        print(f"  {cat}: {count} 条")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="生成多项目 Mock 测试数据")
    parser.add_argument("--project", type=str, default=None, choices=["museum", "enterprise", "all"],
                        help="生成哪个项目的数据（默认: all）")
    args = parser.parse_args()

    projects = ["museum", "enterprise"] if args.project in ("all", None) else [args.project]

    for project in projects:
        if project == "museum":
            data = generate_museum_data()
            output = Path(__file__).resolve().parent.parent / "data" / "raw" / "museum" / "data.json"
            save_data(data, output)
            print_statistics("博物馆项目", data)
        elif project == "enterprise":
            data = generate_enterprise_data()
            output = Path(__file__).resolve().parent.parent / "data" / "raw" / "enterprise" / "data.json"
            save_data(data, output)
            print_statistics("企业项目", data)

    print("\n✅ 所有项目数据生成完成！")
    print("\n后续步骤:")
    print("  # 构建博物馆知识库")
    print("  python scripts/build_knowledge_base.py --project museum")
    print("")
    print("  # 构建企业知识库")
    print("  python scripts/build_knowledge_base.py --project enterprise")
    print("")
    print("  # 启动博物馆问答服务")
    print("  python app.py --project museum")
    print("")
    print("  # 启动企业问答服务")
    print("  python app.py --project enterprise")


if __name__ == "__main__":
    main()