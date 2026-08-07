# Excel (.xlsx) 知识库数据源支持 — 设计文档

> 日期：2026-08-06
> 状态：已批准（用户确认方案 A）
> 关联 bug 条目：bug-109（见 bug-fix-plan.md）

## 1. 背景与需求

多个项目（家博会等）拥有大量 Excel 表格数据（参展商名单、展位信息等），当前系统仅支持
JSON/CSV 结构化数据与多格式文档（PDF/Word/TXT/图片），无法直接使用 `.xlsx`。

需求确认：
- **形态**：表格型（每行一条记录，列 = 字段）
- **列名**：不可穷举（不同项目字段各异），需通用策略——未识别列全部入库可检索
- **接入**：docs 模式（目录自动识别）+ json 模式（`--json-path xxx.xlsx`）双入口
- **多 sheet**：支持，每个 sheet 独立成数据集
- **旧版 .xls**：不支持（仅 .xlsx，openpyxl）

## 2. 方案（方案 A：共享核心 + 双入口委托）

### 2.1 核心实现（`src/data_loader.py`）

1. `DataLoader.SUPPORTED_FORMATS` 增加 `"xlsx"`，`loader_map` 增加 `_load_xlsx`；
2. `_load_xlsx(path)`：openpyxl `load_workbook(path, read_only=True, data_only=True)`
   - 遍历所有 sheet（多 sheet 支持），第一行 = 表头，后续行 = 记录；
   - 单元格值统一转字符串（数字/布尔→str，日期→`YYYY-MM-DD`）；
   - 空行（全部单元格为空）跳过；空列名跳过；
   - 返回 `List[Dict]`（每行一个 dict：列名 → 值）。
3. `_normalize` 增强（通用列策略，解决"列名穷举不完"）：
   - **名称列识别三级**：① 列名命中候选集（`名称/name/标题/title/展商名称/企业名称/公司名称/项目名称/Name/Title` 等）
     → ② 否则取第一个非空列 → ③ 兜底 `"{sheet名}第N行"`；
   - **未识别列全部拼入 description**：`列名：值` 逐行拼接（跳过空值与名称列），
     → 任何列内容均可被全文检索命中；
   - 已识别的结构化字段（category/tags/importance 等）仍走现有字段映射表，单独保留便于过滤；
   - `extra` 保留原始行数据 + `源文件` + `sheet名`。

### 2.2 双入口接入

| 入口 | 改动 | 命令 |
|------|------|------|
| json | `SUPPORTED_FORMATS += "xlsx"` | `--source json --json-path 名单.xlsx` |
| docs | `document_loader.SUPPORTED_EXTENSIONS += ".xlsx"`；`load_all_as_artifacts` 对 .xlsx 文件委托 `DataLoader.load`（直接并入返回的多条 Artifact） | 文件丢进文档目录即可 |
| mixed | 两入口自动生效，无额外改动 | 不额外改动 |

`document_loader.py` 已 `from src.data_loader import Artifact`，无新增循环依赖。

### 2.3 错误处理

- openpyxl 未安装 → `ImportError` 友好提示（与 pypdf 同风格）：`请先安装 openpyxl: pip install openpyxl`；
- 单个 Excel 损坏 → docs 模式下该文件被现有 `load_directory` try/except 捕获跳过，不中断整体构建；
- 空 sheet / 无数据行 → 返回空列表，调用方按"无数据"处理。

### 2.4 依赖

- `requirements.txt` 增加 `openpyxl>=3.1.0`（注释：可选，Excel 数据源支持）。

## 3. 测试策略（全部 mock / 本地构造，不依赖 API）

新增 `tests/test_edge_cases.py::TestExcelSupport`：
1. `DataLoader.load` 解析 .xlsx 返回正确条数与字段；
2. 名称列三级识别（标准键 / 自定义"展商名称" / 第一列兜底）；
3. 未识别列拼入 description（可被检索命中）；
4. 多 sheet 各自独立处理；
5. 空行 / 空列跳过；
6. openpyxl 缺失时友好提示（mock import）；
7. docs 模式 `load_all_as_artifacts` 识别 .xlsx（临时文件构造）；
8. 全量既有 223 项测试保持通过（不影响其他功能模块）。

## 4. 文档落盘

- `bug-fix-plan.md`：追加功能条目 bug-109（根因/方案/风险/验证）；
- `README.md`：支持的文档格式表加 `.xlsx`；数据源模式说明加 Excel；使用示例；
- `project-context.md`：模块清单与功能特性更新。

## 5. 风险

- 低-中。新增独立解析路径，不动现有 JSON/CSV/文档解析逻辑；
- openpyxl 为可选依赖，缺失时仅 Excel 功能不可用，其余不受影响。