"""
Mock 文物数据生成器
生成 50+ 件覆盖各朝代、各类别的文物测试数据，用于端到端测试
"""

import json
import random
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

# ========== 数据池 ==========

DYNASTIES = [
    "新石器时代", "夏代", "商代", "西周", "春秋", "战国",
    "秦代", "西汉", "东汉", "三国", "西晋", "东晋",
    "南北朝", "隋代", "唐代", "五代十国", "北宋", "南宋",
    "元代", "明代", "清代",
]

CATEGORIES = [
    "青铜器", "陶瓷", "书画", "玉器", "金银器",
    "漆器", "竹木牙角", "珐琅器", "织绣", "石刻",
    "古籍", "钱币", "杂项",
]

MATERIALS = {
    "青铜器": ["青铜", "铜"],
    "陶瓷": ["瓷", "陶", "唐三彩"],
    "书画": ["纸本", "绢本", "绫本"],
    "玉器": ["玉", "翡翠", "和田玉", "青玉", "白玉"],
    "金银器": ["金", "银", "金银"],
    "漆器": ["木胎漆", "夹纻", "雕漆"],
    "竹木牙角": ["竹", "木", "象牙", "犀角"],
    "珐琅器": ["铜胎珐琅", "掐丝珐琅", "画珐琅"],
    "织绣": ["丝", "锦", "缂丝"],
    "石刻": ["石", "碑", "造像"],
    "古籍": ["纸", "竹简", "帛"],
    "钱币": ["铜", "金", "银", "纸币"],
    "杂项": ["混合材质"],
}

MUSEUMS = [
    "中国国家博物馆", "故宫博物院", "台北故宫博物院",
    "上海博物馆", "南京博物院", "陕西省历史博物馆",
    "河南博物院", "湖北省博物馆", "湖南省博物馆",
    "浙江省博物馆", "辽宁省博物馆", "甘肃省博物馆",
    "河北省博物院", "山西省博物院", "四川省博物院",
    "安徽省博物院", "江西省博物馆", "广东省博物馆",
    "云南省博物馆", "新疆维吾尔自治区博物馆",
    "秦始皇兵马俑博物馆", "三星堆博物馆",
    "洛阳博物馆", "西安博物院", "苏州博物馆",
]

# 朝代分组（用于推荐全覆盖）
DYNASTY_GROUPS = {
    "先秦": ["新石器时代", "夏代", "商代", "西周", "春秋", "战国"],
    "秦汉": ["秦代", "西汉", "东汉"],
    "魏晋南北朝": ["三国", "西晋", "东晋", "南北朝"],
    "隋唐": ["隋代", "唐代"],
    "宋元": ["北宋", "南宋", "元代"],
    "明清": ["明代", "清代"],
}

# ========== 文物名录（50件） ==========

ARTIFACT_TEMPLATES = [
    # === 青铜器 (10件) ===
    {
        "name": "司母戊鼎（后母戊鼎）",
        "dynasty": "商代", "category": "青铜器",
        "description": "目前已知中国古代最重的青铜器，重832.84公斤，高133厘米。鼎身呈长方形，口沿很厚，轮廓方直。鼎腹内壁铸有'后母戊'三字。",
        "significance": "代表了商代青铜铸造技术的巅峰，是中华文明的重要象征。2002年被列入首批禁止出国（境）展览文物目录。",
        "importance": 5, "tags": ["国宝", "青铜器", "商代", "礼器", "镇馆之宝"]
    },
    {
        "name": "四羊方尊",
        "dynasty": "商代", "category": "青铜器",
        "description": "高58.3厘米，重约34.5公斤。方口，大沿，长颈，高圈足。尊的四角各塑一只羊，肩部四角还有卷角羊头。",
        "significance": "商代青铜器中的精品，造型独特，工艺精湛，代表了商代晚期青铜铸造的高超水平。",
        "importance": 5, "tags": ["国宝", "青铜器", "商代", "礼器", "酒器"]
    },
    {
        "name": "毛公鼎",
        "dynasty": "西周", "category": "青铜器",
        "description": "高53.8厘米，口径47.9厘米，重34.5公斤。鼎内壁铸有铭文32行499字，是现存青铜器中铭文最长的一件。",
        "significance": "铭文是研究西周政治制度的重要文献，被称为'青铜器第一重器'。与司母戊鼎、大盂鼎并称'青铜三宝'。",
        "importance": 5, "tags": ["国宝", "青铜器", "西周", "铭文", "青铜三宝"]
    },
    {
        "name": "大盂鼎",
        "dynasty": "西周", "category": "青铜器",
        "description": "高101.9厘米，口径77.8厘米，重153.5公斤。鼎内壁铸有铭文19行291字，记载了周康王对贵族盂的训诰和赏赐。",
        "significance": "造型雄伟庄重，铭文书法古朴典雅，是西周青铜器的代表作，与毛公鼎、司母戊鼎并称'青铜三宝'。",
        "importance": 5, "tags": ["国宝", "青铜器", "西周", "礼器", "青铜三宝"]
    },
    {
        "name": "曾侯乙编钟",
        "dynasty": "战国", "category": "青铜器",
        "description": "全套编钟共65件，分三层八组悬挂，最大钟高153.4厘米，重203.6公斤。音域跨五个半八度，十二个半音齐备。",
        "significance": "世界上数量最多、保存最完整的青铜编钟，证明战国时期中国已有完整十二音律体系，比西方早约1800年。被誉为'世界第八大奇迹'。",
        "importance": 5, "tags": ["国宝", "青铜器", "战国", "乐器", "编钟"]
    },
    {
        "name": "越王勾践剑",
        "dynasty": "春秋", "category": "青铜器",
        "description": "通长55.7厘米，宽4.6厘米。剑身布满黑色菱形暗纹，剑格镶蓝色琉璃和绿松石。剑身刻有'越王鸠浅自作用剑'八字鸟篆铭文。出土时毫无锈蚀，依然锋利。",
        "significance": "历经两千多年依然锋利不锈，代表了春秋时期越国青铜铸造技术的最高水平，菱形暗纹工艺至今难以完全复制。",
        "importance": 5, "tags": ["国宝", "青铜器", "春秋", "兵器", "越王勾践"]
    },
    {
        "name": "马踏飞燕（铜奔马）",
        "dynasty": "东汉", "category": "青铜器",
        "description": "高34.5厘米，长45厘米。造型为骏马三足腾空、一足踏飞燕，展现了汉代青铜铸造的卓越技艺和丰富想象力。",
        "significance": "中国古代雕塑艺术的杰出代表，1983年被确定为中国旅游标志。构思巧妙，造型生动，是汉代青铜艺术的巅峰之作。",
        "importance": 5, "tags": ["国宝", "青铜器", "东汉", "雕塑", "旅游标志"]
    },
    {
        "name": "三星堆青铜面具",
        "dynasty": "商代", "category": "青铜器",
        "description": "三星堆遗址出土的青铜面具，造型夸张奇特，双目突出，耳朵极大，具有鲜明的古蜀文明特色。最大的一件宽达138厘米。",
        "significance": "代表了古蜀文明独特的青铜文化，与中原青铜文明迥然不同，是研究中国古代多元文明的重要实物。",
        "importance": 5, "tags": ["国宝", "青铜器", "商代", "三星堆", "古蜀文明"]
    },
    {
        "name": "利簋",
        "dynasty": "西周", "category": "青铜器",
        "description": "高28厘米，口径22厘米。簋内底铸有铭文4行32字，记载了周武王伐商纣王的历史事件。",
        "significance": "铭文记载了武王伐纣的具体日期，为夏商周断代工程提供了关键证据，是西周青铜器断代的标准器。",
        "importance": 5, "tags": ["国宝", "青铜器", "西周", "礼器", "断代"]
    },
    {
        "name": "秦始皇陵铜车马",
        "dynasty": "秦代", "category": "青铜器",
        "description": "秦始皇陵出土的铜车马，共两乘，每乘车马由3000多个部件组成。1号车为立车，2号车为安车。工艺极为精湛。",
        "significance": "被誉为'青铜之冠'，代表了秦代青铜铸造和金属加工工艺的最高水平，是研究秦代车制的重要实物。",
        "importance": 5, "tags": ["国宝", "青铜器", "秦代", "秦始皇陵", "车马"]
    },

    # === 陶瓷 (8件) ===
    {
        "name": "元青花萧何月下追韩信图梅瓶",
        "dynasty": "元代", "category": "陶瓷",
        "description": "高44.1厘米，小口、短颈、丰肩、瘦底。瓶身绘有'萧何月下追韩信'历史故事图案，青花发色浓艳，画工精湛。",
        "significance": "元青花瓷中的极品，代表了元代青花瓷器的最高水平。存世仅此一件，极为珍贵。",
        "importance": 5, "tags": ["国宝", "瓷器", "元代", "青花", "元青花"]
    },
    {
        "name": "汝窑天青釉弦纹樽",
        "dynasty": "宋代", "category": "陶瓷",
        "description": "高12.9厘米，口径18.5厘米。直口、平底，筒形腹，外壁饰三道弦纹。通体施天青釉，釉色青中泛蓝，有细密开片纹。",
        "significance": "汝窑为宋代五大名窑之首，烧造时间仅约20年，传世作品不足百件。此件弦纹樽是汝窑的代表作，代表了宋代单色釉瓷器的最高审美境界。",
        "importance": 5, "tags": ["国宝", "瓷器", "宋代", "汝窑", "五大名窑"]
    },
    {
        "name": "唐三彩骆驼载乐俑",
        "dynasty": "唐代", "category": "陶瓷",
        "description": "高58.4厘米，长43.4厘米。骆驼昂首挺立，背上驮着三个胡人乐俑和两个汉人舞俑。施黄、绿、褐三彩釉。",
        "significance": "唐三彩中的珍品，反映了唐代丝绸之路的繁荣和中外文化交流的盛况，是唐代陶塑艺术的代表作。",
        "importance": 5, "tags": ["国宝", "陶器", "唐代", "唐三彩", "丝绸之路"]
    },
    {
        "name": "秦陵兵马俑",
        "dynasty": "秦代", "category": "陶瓷",
        "description": "已出土陶俑、陶马约8000件，包括步兵俑、骑兵俑、车兵俑、将军俑等。每个陶俑身高约1.8米，面部表情各不相同，栩栩如生。",
        "significance": "被誉为'世界第八大奇迹'，反映秦代军事制度、服饰文化和雕塑艺术的最高水平。1987年被列入世界文化遗产。",
        "importance": 5, "tags": ["国宝", "陶器", "秦代", "兵马俑", "世界文化遗产"]
    },
    {
        "name": "成化斗彩鸡缸杯",
        "dynasty": "明代", "category": "陶瓷",
        "description": "高3.4厘米，口径8.3厘米。敞口、浅腹、卧足。杯外壁绘有子母鸡图，画面生动活泼。斗彩工艺精湛，色彩艳丽。",
        "significance": "成化斗彩的代表作，2014年拍出2.8亿港元天价。代表了明代成化时期斗彩瓷器的最高水平。",
        "importance": 5, "tags": ["国宝", "瓷器", "明代", "斗彩", "成化"]
    },
    {
        "name": "青花釉里红开光镂空牡丹纹盖罐",
        "dynasty": "元代", "category": "陶瓷",
        "description": "高42.3厘米，口径15.2厘米。罐身采用青花和釉里红两种釉下彩工艺，结合开光、镂空等装饰技法，工艺极为复杂。",
        "significance": "元代青花釉里红瓷器的杰作，代表了元代制瓷工艺的最高水平，存世仅此一件。",
        "importance": 5, "tags": ["国宝", "瓷器", "元代", "青花釉里红", "镂空"]
    },
    {
        "name": "珐琅彩雉鸡牡丹纹碗",
        "dynasty": "清代", "category": "陶瓷",
        "description": "高7.6厘米，口径15.2厘米。碗外壁以珐琅彩绘有雉鸡牡丹图案，色彩艳丽，画工精细。底有'乾隆年制'款识。",
        "significance": "清代珐琅彩瓷器的代表作，代表了清代宫廷瓷器工艺的最高水平，是乾隆时期瓷器艺术的典范。",
        "importance": 4, "tags": ["瓷器", "清代", "珐琅彩", "乾隆"]
    },
    {
        "name": "钧窑玫瑰紫釉葵花式花盆",
        "dynasty": "宋代", "category": "陶瓷",
        "description": "高18.5厘米，口径26.8厘米。花盆呈葵花式，施玫瑰紫釉，釉色绚丽多变，有'入窑一色，出窑万彩'之誉。",
        "significance": "钧窑以窑变釉色闻名，玫瑰紫釉是钧窑中最名贵的品种之一。此件花盆造型优美，釉色绝佳，是钧窑的代表作。",
        "importance": 4, "tags": ["瓷器", "宋代", "钧窑", "五大名窑"]
    },

    # === 书画 (8件) ===
    {
        "name": "清明上河图",
        "dynasty": "北宋", "category": "书画",
        "description": "北宋画家张择端创作的风俗画长卷，宽24.8厘米，长528.7厘米。以长卷形式记录北宋都城东京的城市面貌和社会各阶层生活。",
        "significance": "中国风俗画的巅峰之作，'中国十大传世名画'之一。是研究北宋城市经济、社会结构、建筑风格的百科全书式珍贵资料。",
        "importance": 5, "tags": ["国宝", "书画", "北宋", "风俗画", "十大传世名画"]
    },
    {
        "name": "富春山居图",
        "dynasty": "元代", "category": "书画",
        "description": "元代画家黄公望晚年为师弟所绘，纵33厘米，横636.9厘米。以浙江富春江为背景，用墨淡雅，布置疏密得当。",
        "significance": "中国山水画的巅峰之作，被誉为'画中之兰亭'。因火焚分为两段，前段藏于浙江省博物馆，后段藏于台北故宫博物院。",
        "importance": 5, "tags": ["国宝", "书画", "元代", "山水画", "十大传世名画"]
    },
    {
        "name": "千里江山图",
        "dynasty": "北宋", "category": "书画",
        "description": "北宋王希孟创作的青绿山水画长卷，纵51.5厘米，横1191.5厘米。以石青、石绿等矿物质颜料绘制，色彩瑰丽。",
        "significance": "中国青绿山水画的巅峰之作，王希孟年仅18岁即创作此画，是中國美術史上的傳奇。2017年故宫特展引发'千里江山图热'。",
        "importance": 5, "tags": ["国宝", "书画", "北宋", "山水画", "青绿山水"]
    },
    {
        "name": "兰亭序（神龙本）",
        "dynasty": "唐代", "category": "书画",
        "description": "唐代冯承素摹本，纵24.5厘米，横69.9厘米。为王羲之《兰亭序》的临摹本，因卷有唐中宗'神龙'年号印而得名。",
        "significance": "《兰亭序》被誉为'天下第一行书'，真迹已失传，神龙本是最接近原作的摹本，是学习王羲之书法的重要范本。",
        "importance": 5, "tags": ["国宝", "书画", "唐代", "书法", "王羲之"]
    },
    {
        "name": "韩熙载夜宴图",
        "dynasty": "五代十国", "category": "书画",
        "description": "南唐画家顾闳中创作，描绘了官员韩熙载在家中设宴行乐的场面。全卷分为五段，人物刻画细腻传神。",
        "significance": "五代人物画的杰作，是中国古代绘画史上最著名的叙事性绘画之一，对研究五代时期的社会生活和服饰文化有重要价值。",
        "importance": 5, "tags": ["国宝", "书画", "五代", "人物画", "十大传世名画"]
    },
    {
        "name": "女史箴图（唐代摹本）",
        "dynasty": "唐代", "category": "书画",
        "description": "东晋顾恺之创作，唐代摹本。纵24.8厘米，横348.2厘米。根据西晋张华《女史箴》一文而绘，共11段。",
        "significance": "中国绘画史上最早的叙事性绘画之一，人物线条流畅，'春蚕吐丝'的笔法代表了早期人物画的最高成就。现藏于大英博物馆。",
        "importance": 5, "tags": ["国宝", "书画", "东晋", "人物画", "顾恺之"]
    },
    {
        "name": "祭侄文稿",
        "dynasty": "唐代", "category": "书画",
        "description": "唐代书法家颜真卿的行书作品，纵28.3厘米，横75.5厘米。是颜真卿为追祭在安史之乱中牺牲的侄子所写。",
        "significance": "被誉为'天下第二行书'，与王羲之《兰亭序》并列。情感真挚，笔法苍劲，是颜真卿书法艺术的巅峰之作。",
        "importance": 5, "tags": ["国宝", "书画", "唐代", "书法", "颜真卿"]
    },
    {
        "name": "洛神赋图（宋代摹本）",
        "dynasty": "宋代", "category": "书画",
        "description": "东晋顾恺之根据曹植《洛神赋》创作，宋代摹本。描绘了曹植与洛神相遇、相恋、分离的浪漫故事。",
        "significance": "中国绘画史上最著名的爱情主题画作，人物构图精妙，'人大于山，水不容泛'的早期山水画特点明显。",
        "importance": 5, "tags": ["国宝", "书画", "东晋", "人物画", "顾恺之"]
    },

    # === 玉器 (6件) ===
    {
        "name": "翠玉白菜",
        "dynasty": "清代", "category": "玉器",
        "description": "长18.7厘米，宽9.1厘米。由一块半白半绿的翠玉雕琢而成，白色成菜帮，绿色成菜叶，叶上雕有螽斯和蝗虫。",
        "significance": "俏色巧雕工艺的巅峰之作，台北故宫博物院三大镇馆之宝之一。据传为光绪皇帝妃子瑾妃的嫁妆，寓意清白、多子多孙。",
        "importance": 5, "tags": ["国宝", "玉器", "清代", "翡翠", "台北故宫博物院"]
    },
    {
        "name": "金缕玉衣",
        "dynasty": "西汉", "category": "玉器",
        "description": "中山靖王刘胜金缕玉衣长1.88米，共用玉片2498片，金丝约1100克。玉片之间用金丝编缀，包括头罩、上身、袖子、手套、裤筒和鞋六部分。",
        "significance": "中国考古史上首次发现的金缕玉衣，也是保存最完整的金缕玉衣之一，反映了汉代皇室贵族的丧葬制度。",
        "importance": 5, "tags": ["国宝", "玉器", "西汉", "丧葬", "金缕玉衣"]
    },
    {
        "name": "玉猪龙",
        "dynasty": "新石器时代", "category": "玉器",
        "description": "红山文化代表性玉器，高约15厘米。呈C形，头部似猪，身体蜷曲。是红山文化中最高等级的祭祀用玉。",
        "significance": "红山文化玉器的代表，是研究中华文明起源的重要实物。玉猪龙的形象对后世龙文化的形成产生了重要影响。",
        "importance": 5, "tags": ["国宝", "玉器", "新石器时代", "红山文化", "祭祀"]
    },
    {
        "name": "良渚玉琮",
        "dynasty": "新石器时代", "category": "玉器",
        "description": "良渚文化代表性玉器，外方内圆，高约30厘米。表面刻有精美的神人兽面纹，是良渚玉器中最高等级的礼器。",
        "significance": "良渚玉琮是史前玉器工艺的巅峰之作，2019年良渚古城遗址被列入世界文化遗产。玉琮对后世中国玉文化影响深远。",
        "importance": 5, "tags": ["国宝", "玉器", "新石器时代", "良渚文化", "礼器"]
    },
    {
        "name": "大禹治水图玉山",
        "dynasty": "清代", "category": "玉器",
        "description": "高224厘米，宽96厘米，重约5300公斤。以新疆和田青玉雕琢，描绘了大禹治水的场景，是中國最大的玉雕作品。",
        "significance": "中国玉雕史上体积最大、重量最重的玉雕作品，耗时十余年完成。代表了清代玉雕工艺的最高水平。",
        "importance": 5, "tags": ["国宝", "玉器", "清代", "玉雕", "大禹治水"]
    },
    {
        "name": "曾侯乙玉璜",
        "dynasty": "战国", "category": "玉器",
        "description": "曾侯乙墓出土的玉璜，长13.5厘米，宽4.8厘米。双面雕刻，纹饰精美，为战国玉器中的精品。",
        "significance": "代表了战国时期玉器工艺的高超水平，是研究战国时期礼仪制度和审美观念的重要实物。",
        "importance": 4, "tags": ["玉器", "战国", "曾侯乙", "礼器"]
    },

    # === 金银器 (4件) ===
    {
        "name": "唐代鎏金舞马衔杯银壶",
        "dynasty": "唐代", "category": "金银器",
        "description": "高18.5厘米，口径2.2厘米。壶身呈皮囊形，鎏金工艺精湛。壶身浮雕一匹舞马，口衔酒杯，后腿弯曲，作舞蹈状。",
        "significance": "唐代金银器的代表作，印证了唐玄宗时期宫廷中'舞马'表演的存在。工艺精湛，造型独特，是唐代中外文化交流的见证。",
        "importance": 5, "tags": ["国宝", "金银器", "唐代", "鎏金", "舞马"]
    },
    {
        "name": "何家村窖藏金银器",
        "dynasty": "唐代", "category": "金银器",
        "description": "1970年陕西西安何家村出土，共1000余件金银器，包括金银碗、盘、杯、壶等。工艺极其精湛，纹饰繁复华丽。",
        "significance": "唐代金银器最大的考古发现之一，被誉为'唐代金银器的宝库'。反映了唐代高度发达的手工业水平和贵族奢华生活。",
        "importance": 5, "tags": ["国宝", "金银器", "唐代", "窖藏", "何家村"]
    },
    {
        "name": "明代金翼善冠",
        "dynasty": "明代", "category": "金银器",
        "description": "定陵出土，明万历皇帝的金冠。高24厘米，用极细的金丝编织而成，冠上饰有两条金龙。工艺令人叹为观止。",
        "significance": "明代金银细工的代表作，金丝编织工艺达到了登峰造极的水平，是研究明代帝王服饰制度的重要实物。",
        "importance": 5, "tags": ["国宝", "金银器", "明代", "定陵", "万历"]
    },
    {
        "name": "西汉错金银云纹铜犀尊",
        "dynasty": "西汉", "category": "金银器",
        "description": "高34.1厘米，长58.1厘米。以犀牛为造型，通体饰错金银云纹，工艺精湛，造型生动逼真。",
        "significance": "汉代错金银工艺的代表作，犀牛造型在中国古代极为罕见，反映了汉代与南亚地区的交流。",
        "importance": 5, "tags": ["国宝", "金银器", "西汉", "错金银", "犀牛"]
    },

    # === 漆器 (3件) ===
    {
        "name": "战国彩绘漆木虎座鸟架鼓",
        "dynasty": "战国", "category": "漆器",
        "description": "湖北江陵望山楚墓出土。以两只卧虎为底座，虎背上立两只长腿鸟，鸟尾相连成鼓架，造型奇特，色彩艳丽。",
        "significance": "楚文化漆器的代表作，体现了楚国浪漫奔放的艺术风格和精湛的漆艺水平。",
        "importance": 5, "tags": ["国宝", "漆器", "战国", "楚文化", "乐器"]
    },
    {
        "name": "元代剔红牡丹纹圆盒",
        "dynasty": "元代", "category": "漆器",
        "description": "直径12.5厘米，高5.8厘米。盒面雕剔红牡丹纹，刀法圆润，层次分明，漆色纯正。",
        "significance": "元代雕漆工艺的代表作，剔红工艺在元代达到高峰，此件作品代表了元代雕漆的最高水平。",
        "importance": 4, "tags": ["漆器", "元代", "剔红", "雕漆"]
    },
    {
        "name": "明代黑漆描金山水人物纹盒",
        "dynasty": "明代", "category": "漆器",
        "description": "长28.5厘米，宽18.2厘米。黑漆底上以金粉描绘山水人物图案，画工精细，色彩华贵。",
        "significance": "明代描金漆器的代表作，融合了绘画艺术与漆器工艺，是明代漆器工艺的精品。",
        "importance": 4, "tags": ["漆器", "明代", "描金", "山水"]
    },

    # === 石刻 (3件) ===
    {
        "name": "云冈石窟大佛",
        "dynasty": "北魏", "category": "石刻",
        "description": "云冈石窟第20窟的露天大佛，高约13.7米。佛像面容丰圆，深目高鼻，体现了佛教艺术中国化的早期特征。",
        "significance": "云冈石窟是世界文化遗产，代表了北魏时期佛教石刻艺术的最高成就。大佛是云冈石窟的标志性造像。",
        "importance": 5, "tags": ["国宝", "石刻", "北魏", "佛教", "云冈石窟", "世界文化遗产"]
    },
    {
        "name": "昭陵六骏",
        "dynasty": "唐代", "category": "石刻",
        "description": "唐太宗李世民陵墓前的六匹战马浮雕，分别名为：飒露紫、拳毛𫘧、青骓、什伐赤、特勒骠、白蹄乌。其中两件现藏于美国宾夕法尼亚大学博物馆。",
        "significance": "唐代浮雕艺术的杰作，造型生动，气势磅礴。是研究唐代历史和艺术的重要实物，也是中国文物流失海外的标志性案例。",
        "importance": 5, "tags": ["国宝", "石刻", "唐代", "浮雕", "昭陵"]
    },
    {
        "name": "龙门石窟卢舍那大佛",
        "dynasty": "唐代", "category": "石刻",
        "description": "龙门石窟奉先寺的卢舍那大佛，高17.14米。佛像面容丰满，慈祥庄严，是唐代佛教造像的典范。",
        "significance": "龙门石窟是世界文化遗产，卢舍那大佛是中国佛教造像艺术的巅峰之作，被誉为'东方蒙娜丽莎'。",
        "importance": 5, "tags": ["国宝", "石刻", "唐代", "佛教", "龙门石窟", "世界文化遗产"]
    },

    # === 古籍 (3件) ===
    {
        "name": "永乐大典",
        "dynasty": "明代", "category": "古籍",
        "description": "全书22877卷，目录60卷，分装11095册，约3.7亿字。汇集古今图书七八千种，是中国古代最大的百科全书。",
        "significance": "中国古代最大的百科全书，保存了14世纪以前中国历史地理、文学艺术、哲学宗教和百科文献。现今仅存400余册。",
        "importance": 5, "tags": ["国宝", "古籍", "明代", "百科全书", "永乐大典"]
    },
    {
        "name": "四库全书",
        "dynasty": "清代", "category": "古籍",
        "description": "清代乾隆年间编纂的大型丛书，收录图书3461种，79309卷，约8亿字。分经、史、子、集四部。",
        "significance": "中国古代最大的官修丛书，对中国古代文献的保存和整理做出了巨大贡献。原抄七部，现存三部半。",
        "importance": 5, "tags": ["国宝", "古籍", "清代", "丛书", "四库全书"]
    },
    {
        "name": "敦煌遗书",
        "dynasty": "唐代", "category": "古籍",
        "description": "1900年敦煌莫高窟藏经洞发现的数万件珍贵文献，包括佛经、道经、儒家经典、史籍、诗词、契约等，涵盖4-11世纪。",
        "significance": "20世纪最重大的考古发现之一，被誉为'古代文献的宝库'。对研究中国古代宗教、历史、文学、艺术具有不可估量的价值。",
        "importance": 5, "tags": ["国宝", "古籍", "唐代", "敦煌", "藏经洞"]
    },

    # === 杂项 (5件) ===
    {
        "name": "司南",
        "dynasty": "汉代", "category": "杂项",
        "description": "汉代用于指示方向的仪器，由青铜地盘和天然磁石制成的勺形指南器组成。地盘上刻有天干地支和八卦。",
        "significance": "中国古代四大发明之一——指南针的早期形式，是中国古代科技成就的重要代表。",
        "importance": 4, "tags": ["杂项", "汉代", "科技", "指南针", "四大发明"]
    },
    {
        "name": "宋代针灸铜人",
        "dynasty": "宋代", "category": "杂项",
        "description": "北宋天圣年间铸造的针灸教学模型，与真人等大，全身标注354个穴位。是中医针灸教学的重要工具。",
        "significance": "世界上最早的针灸教学模型，代表了宋代医学教育的高度发达，是中医针灸学的重要历史见证。",
        "importance": 5, "tags": ["国宝", "杂项", "宋代", "针灸", "中医"]
    },
    {
        "name": "汉代长信宫灯",
        "dynasty": "西汉", "category": "杂项",
        "description": "高48厘米，鎏金青铜器。宫女跪坐持灯造型，灯盘、灯罩可转动开合以调节光照方向和亮度。宫女体内中空，可容纳烟尘。",
        "significance": "汉代灯具的杰作，设计巧妙，兼具实用性和艺术性。体现了汉代工匠的智慧，是环保设计的早期典范。",
        "importance": 5, "tags": ["国宝", "杂项", "西汉", "灯具", "鎏金"]
    },
    {
        "name": "唐代大雁塔",
        "dynasty": "唐代", "category": "石刻",
        "description": "位于西安大慈恩寺内，高64.5米，共七层。玄奘法师为保存从印度带回的佛经而主持修建。塔身有著名的唐代书法碑刻。",
        "significance": "唐代佛教建筑的代表作，是玄奘西行取经的重要历史见证。塔内珍藏的《大唐三藏圣教序》碑刻是书法艺术珍品。",
        "importance": 5, "tags": ["国宝", "石刻", "唐代", "佛教", "玄奘", "大雁塔"]
    },
    {
        "name": "宋代天文图碑",
        "dynasty": "宋代", "category": "石刻",
        "description": "苏州文庙保存的宋代天文图石刻，高约2米。图上刻有1434颗恒星，是世界上现存最古老的石刻天文图之一。",
        "significance": "中国古代天文学成就的重要见证，代表了宋代天文学的观测水平。对研究中国古代天文学史具有重要价值。",
        "importance": 4, "tags": ["石刻", "宋代", "天文", "科技", "星图"]
    },
]


def generate_mock_artifacts(count: int = 50, seed: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    生成 Mock 文物数据
    如果 count > 内置模板数量，会随机组合生成更多

    Args:
        count: 文物数量
        seed: 随机种子（设为固定值可使数据可复现）
    """
    if seed is not None:
        random.seed(seed)
    artifacts = []

    # 先使用模板数据
    for i, template in enumerate(ARTIFACT_TEMPLATES):
        if i >= count:
            break
        artifact = {
            "name": template["name"],
            "dynasty": template["dynasty"],
            "category": template["category"],
            "material": random.choice(MATERIALS.get(template["category"], ["未知"])),
            "provenance": f"出土于{random.choice(['河南', '陕西', '湖北', '湖南', '江苏', '浙江', '甘肃', '四川', '山西', '河北'])}省",
            "location": random.choice(MUSEUMS),
            "description": template["description"],
            "historical_significance": template["significance"],
            "cultural_value": f"{template['name']}具有极高的历史、艺术和科学价值，是中华文明的重要瑰宝。",
            "tags": template["tags"],
            "importance": template["importance"],
        }
        artifacts.append(artifact)

    # 如果 count > 模板数量，随机组合生成更多
    if count > len(ARTIFACT_TEMPLATES):
        extra_count = count - len(ARTIFACT_TEMPLATES)
        logger.info(f"模板不足，将随机生成 {extra_count} 件补充文物")
        for i in range(extra_count):
            dynasty = random.choice(DYNASTIES)
            category = random.choice(CATEGORIES)
            material = random.choice(MATERIALS.get(category, ["未知"]))
            museum = random.choice(MUSEUMS)
            artifact = {
                "name": f"{random.choice(['彩绘', '鎏金', '雕花', '素面', '刻铭', '嵌玉', '描金', '剔红'])}"
                        f"{random.choice(['双耳', '四足', '三足', '圈足', '平底', '敞口', '敛口', '直壁'])}"
                        f"{random.choice(['鼎', '尊', '壶', '瓶', '碗', '盘', '杯', '罐', '盒', '炉', '灯', '镜'])}"
                        f"（{chr(0x4E00 + (i % 6000))}号）",
                "dynasty": dynasty,
                "category": category,
                "material": material,
                "provenance": f"出土于{random.choice(['河南', '陕西', '湖北', '湖南', '江苏', '浙江'])}省"
                              f"{random.choice(['洛阳', '西安', '武汉', '长沙', '南京', '杭州'])}",
                "location": museum,
                "description": f"这是一件{dynasty}时期的{category}文物，采用{material}材质制作。"
                              f"器型规整，工艺精湛，具有鲜明的时代特征。",
                "historical_significance": f"该文物反映了{dynasty}时期{category}制作工艺的水平，"
                                          f"对研究当时的社会经济和文化艺术具有重要参考价值。",
                "cultural_value": f"该文物具有较高的历史价值和艺术价值，是研究{dynasty}时期物质文化的重要实物。",
                "tags": [category, dynasty, "文物"],
                "importance": random.randint(3, 5),
            }
            artifacts.append(artifact)

    return artifacts


def save_mock_data(artifacts: List[Dict[str, Any]], output_path: Path) -> None:
    """保存 Mock 数据到 JSON 文件"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(artifacts, f, ensure_ascii=False, indent=2)
    logger.info(f"已保存 {len(artifacts)} 件 Mock 文物数据到: {output_path}")


def print_statistics(artifacts: List[Dict[str, Any]]) -> None:
    """打印数据统计信息"""
    from collections import Counter

    dynasty_counter = Counter()
    category_counter = Counter()
    importance_counter = Counter()

    for a in artifacts:
        dynasty_counter[a["dynasty"]] += 1
        category_counter[a["category"]] += 1
        importance_counter[a["importance"]] += 1

    print("\n" + "=" * 60)
    print("📊 Mock 文物数据统计")
    print("=" * 60)
    print(f"\n📦 总文物数: {len(artifacts)}")

    print(f"\n📅 朝代分布:")
    for dynasty, count in sorted(dynasty_counter.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * count
        print(f"  {dynasty}: {count} {bar}")

    print(f"\n🏷️ 类别分布:")
    for cat, count in sorted(category_counter.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * count
        print(f"  {cat}: {count} {bar}")

    print(f"\n⭐ 重要性分布:")
    for imp in sorted(importance_counter.keys(), reverse=True):
        count = importance_counter[imp]
        bar = "█" * count
        print(f"  {'★' * imp}: {count} {bar}")

    # 朝代组覆盖统计
    print(f"\n📋 朝代组覆盖:")
    for group_name, dynasties_in_group in DYNASTY_GROUPS.items():
        covered = sum(1 for d in dynasty_counter if d in dynasties_in_group)
        total = len(dynasties_in_group)
        print(f"  {group_name}: {covered}/{total} 个朝代被覆盖")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="生成 Mock 文物测试数据")
    parser.add_argument(
        "-n", "--count", type=int, default=50,
        help="生成的文物数量（默认: 50）"
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="输出文件路径（默认: data/raw/artifacts.json）"
    )
    parser.add_argument(
        "--stats", action=argparse.BooleanOptionalAction, default=True,
        help="打印统计信息（--no-stats 关闭）"
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="随机种子（设为固定值可使数据可复现）"
    )
    args = parser.parse_args()

    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(__file__).resolve().parent.parent / "data" / "raw" / "artifacts.json"

    # 生成数据
    logger.info(f"正在生成 {args.count} 件 Mock 文物数据...")
    artifacts = generate_mock_artifacts(count=args.count, seed=args.seed)

    # 保存数据
    save_mock_data(artifacts, output_path)

    # 打印统计
    if args.stats:
        print_statistics(artifacts)

    logger.info("Mock 数据生成完成！")
    print(f"\n💡 提示: 现在可以运行以下命令构建知识库:")
    print(f"  python scripts/build_knowledge_base.py")


if __name__ == "__main__":
    main()