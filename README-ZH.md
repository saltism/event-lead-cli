# Event Lead CLI — 操作手册

一个命令行工具，将展会原始线索数据处理为可交付给销售团队的完整套件。支持从 Google Form、展会扫码系统、HubSpot 名片扫描等多个数据源读取 CSV 和 Excel 文件，经过清洗、去重、LLM 评分与分组后，输出三个文件供销售直接使用。

> English version: [README.md](README.md)

---

## CLI 使用步骤（实操版）

### 0）只做一次：安装

```bash
git clone https://github.com/saltism/event-lead-cli.git
cd event-lead-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1）每场活动：标准流程

```bash
# 先把活动 CSV/XLSX 放进 data/

# 生成配置（event 或 meetup 二选一）
python -m event_leads init-config --type event --name "活动名称" --date "2026-06-15" --location "Singapore"
# python -m event_leads init-config --type meetup --name "Meetup 名称" --date "2026-06-20" --location "Tokyo"

# 设置 OpenAI key
export OPENAI_API_KEY='sk-...'

# 可选：先自检
./scripts/smoke-test.sh configs/活动名称.yaml

# 开跑
./run_enrich.sh configs/活动名称.yaml
```

### 2）如果还有名片照片

```bash
# 把名片图片放进 data/cards/
python -m event_leads cards-ocr-and-run configs/活动名称.yaml --input-dir data/cards --output-csv data/business-card.csv
```

### 3）结果和续跑

```bash
# 输出在 configs/output/
# - *-leads.csv
# - *-report.md
# - *-email-drafts.md

# 中断后续跑
./run_enrich.sh configs/活动名称.yaml --resume
```

---

## 输出文件

每次运行后，`configs/output/` 目录下会生成三个文件：

| 文件 | 对象 | 内容 |
|------|------|------|
| `{前缀}-leads.csv` | BD / 销售 | 清洗后的完整线索列表，含 `_segment` 分组列，可直接导入 HubSpot |
| `{前缀}-report.md` | BD / 销售 | 各分组说明、跟进建议、紧凑评分表，报告语种按线索语种自动选择 |
| `{前缀}-email-drafts.md` | BD / 销售 | 按分组和语言自动生成的跟进邮件草稿（英文 / 日文 / 繁体中文） |

---

## 环境要求

- Python 3.9 或以上版本
- OpenAI API Key（默认使用 GPT-4o-mini）
- macOS 或 Linux

## 关于 LLM 选型

工具默认使用 **GPT-4o-mini**，选择它的原因是单 token 费用低——处理几百条线索需要多次 LLM 调用，用更贵的模型成本会明显上升。

**换用其他 OpenAI-compatible API**（如 Azure OpenAI、Groq、Together AI，或本地 Ollama 的兼容接口），无需改动任何代码，只需在运行前设置两个环境变量：

```bash
export OPENAI_API_KEY="该服务商的 Key"
export OPENAI_BASE_URL="https://该服务商的接口地址/v1"
```

**换用 Anthropic 或 Gemini**，需要修改 `event_leads/enrich.py` 中的两个函数（约第 213 行）：

```python
# 修改前（OpenAI）
def _get_client():
    return instructor.from_openai(OpenAI())

def _get_async_client():
    return instructor.from_openai(AsyncOpenAI())

# 修改后（Anthropic 示例）
from anthropic import Anthropic, AsyncAnthropic
def _get_client():
    return instructor.from_anthropic(Anthropic())

def _get_async_client():
    return instructor.from_anthropic(AsyncAnthropic())
```

同时需要安装对应 SDK（`pip install anthropic`）并更新 `requirements.txt`。流水线的其余部分无需改动——`instructor` 库统一了各服务商的接口格式。

---

## 与 HubSpot 的配合流程

本工具生成的 CSV 可直接导入 HubSpot。以下为推荐的配置方式。

### 一次性设置（首次使用前）

1. 在 HubSpot 进入 **设置 → 属性 → 联系人属性**
2. 新建一个属性：
   - **名称：** Lead Segment
   - **内部名称：** `lead_segment`
   - **字段类型：** 单行文本（或下拉菜单，可预设分组选项）
3. 可选：新建 `Lead Score`（数字类型）用于导入 `_score_overall` 字段

### 每次导入

1. 用 Excel 或 Google Sheets 打开 `{前缀}-leads.csv`
2. 将列名 `_segment` 改为 `Lead Segment`，将 `_score_overall` 改为 `Lead Score`（与 HubSpot 属性名称对应）
3. 在 HubSpot 中进入 **联系人 → 导入 → 导入文件**
4. 选择 CSV 文件，在导入向导中映射列与属性
5. 导入完成后，用**列表**或**筛选器**按 Lead Segment 分组查看联系人

### 使用邮件草稿

1. 打开 `{前缀}-email-drafts.md`
2. 每个区块按分组和语言标注
3. 在 HubSpot 中按分组筛选联系人，进入联系人记录，将对应草稿粘贴至邮件编辑器
4. 发送前将 `[名字]`、`[公司名]` 等占位符替换为实际内容

---

## 配置说明

### 标准字段名

以下为工具识别的字段名，在 `mapping:` 和 `survey_fields:` 中将数据源列名映射至这些字段：

| 字段名 | 说明 |
|--------|------|
| `name` | 姓名 |
| `email` | 邮箱，用作去重主键 |
| `company_title` | 公司与职位合并在同一列时使用 |
| `company` | 公司名称（单独一列时使用） |
| `title` | 职位（单独一列时使用） |
| `phone` | 电话 |
| `interest_scenario` | 感兴趣的 AI 应用场景 |
| `demo_interest` | 是否希望安排演示 |
| `project_timeline` | 项目或评估时程 |
| `additional_topics` | 问卷自由填写内容 |

### 评分维度

LLM 对每条线索在四个维度上打分（0–10）。每个维度都附带一句评分依据，说明给出该分数的原因。综合得分为加权平均值。权重之和须为 1.0，可在配置文件的 `scoring:` 部分按活动调整。

| 维度 | 默认权重 | 评估内容 | 依据示例 |
|------|----------|----------|----------|
| `company_fit` | 0.25 | 公司规模与行业是否符合 Dify 的目标客户画像 | "上市金融機構，規模與行業高度符合目標客戶" |
| `seniority_match` | 0.25 | 职位是否具备预算决策或采购影响力 | "副總層級，具備採購決策權" |
| `engagement_signal` | 0.35 | 现场的跟进意向积极程度 | "明確要求安排 demo，專案時程 1 個月內" |
| `interest_alignment` | 0.15 | 需求方向与 Dify 核心能力的契合程度 | "關注流程自動化與知識庫，與 Dify 核心能力直接匹配" |

报告会保持紧凑，适合大批量线索阅读。若 BD 需要追查某条线索的给分依据，可直接查看 `{前缀}-leads.csv` 里的 `_score_*_reason` 列。

### 数据源配置说明

| 字段 | 说明 |
|------|------|
| `file` | 相对于 `data_dir` 的文件名 |
| `type` | `csv` 或 `xlsx` |
| `encoding` | 字符编码；Windows Excel 导出常用 `utf-8-sig`，台湾繁体系统常用 `big5` |
| `mapping` | 将数据源列名映射到标准字段名 |
| `survey_fields` | 问卷字段单独列出，便于 LLM 分析 |
| `attendance_status` | `attended`（到场）或 `registered`（报名未到场）|

---

## 常见问题

**提示 `OPENAI_API_KEY` 未设置**

设置 Key 后续跑：

```bash
export OPENAI_API_KEY="sk-..."
./run_enrich.sh configs/新活动名称.yaml --resume
```

**报错 `UnicodeEncodeError: 'ascii' codec can't encode characters ...`**

通常是 `OPENAI_API_KEY` 或 `OPENAI_BASE_URL` 里混入了非 ASCII 字符（例如中文引号、全角符号、不可见空格）。重新用纯文本设置：

```bash
unset OPENAI_API_KEY OPENAI_BASE_URL
export OPENAI_API_KEY='sk-...'
# 如需自定义服务商：
# export OPENAI_BASE_URL='https://your-provider-endpoint/v1'
```

**提示 `CONFIG` 路径不存在**

先生成配置文件，再运行：

```bash
python -m event_leads init-config --type event --name "活动名称" --date "2026-06-15" --location "Your City"
./run_enrich.sh configs/活动名称对应slug.yaml
```

**出现乱码或编码错误**

将对应数据源的 `encoding` 改为 `utf-8-sig`。台湾繁体系统导出的文件可尝试 `big5`。

**邮件语言检测不正确**

语言根据姓名字符和邮箱域名推断：包含中文字符 → `zh_tw`，`.jp` 域名或包含日文字符 → `ja`，其他 → `en`。如需覆盖，在输出 CSV 的 `_lang` 列手动修改后再导入。

**报告语种不是你想要的**

默认按线索主语种自动选择报告语言（`en` / `ja` / `zh_tw`）。如需固定语种，可在配置里指定：

```yaml
output:
  filename_prefix: "新活动名称"
  report_language: "en"   # en / ja / zh_tw / auto
```

---

## 项目结构

```
event-lead-cli/
├── README.md
├── README-ZH.md
├── run_enrich.sh            ← 每次活动的入口脚本
├── requirements.txt
├── data/                    ← 原始 CSV/Excel 数据文件
├── configs/
│   ├── event-template.yaml   ← 展会 / conference 模板
│   ├── meetup-template.yaml  ← meetup / 社群活动模板
│   └── output/
│       ├── checkpoints/     ← 断点续跑的中间状态文件（运行完成后可删除）
│       ├── *-leads.csv
│       ├── *-report.md
│       └── *-email-drafts.md
└── event_leads/
    ├── __main__.py
    ├── pipeline.py
    ├── enrich.py
    └── parsers.py
```
