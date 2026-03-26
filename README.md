# Event Lead CLI

A command-line tool that processes raw event leads into scored, segmented, and email-ready deliverables. It accepts CSV and Excel exports from multiple sources (Google Forms, badge scanners, HubSpot card scans), deduplicates and normalizes the data, applies LLM-based scoring and segmentation, and produces three output files ready for sales follow-up.

> 中文版本：[README-ZH.md](README-ZH.md)

---

## How to run this CLI (practical steps)

### 0) One-time setup

```bash
git clone https://github.com/saltism/event-lead-cli.git
cd event-lead-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1) Per event: standard flow

```bash
# Put event CSV/XLSX files into data/

# Generate config (event or meetup)
python -m event_leads init-config --type event --name "Your Event Name" --date "2026-06-15" --location "Singapore"
# python -m event_leads init-config --type meetup --name "Your Meetup Name" --date "2026-06-20" --location "Tokyo"

# Set OpenAI key
export OPENAI_API_KEY='sk-...'

# Optional preflight check
./scripts/smoke-test.sh configs/your-event-name.yaml

# Run pipeline
./run_enrich.sh configs/your-event-name.yaml
```

### 2) If you also have business card photos

```bash
# Put card images into data/cards/
python -m event_leads cards-ocr-and-run configs/your-event-name.yaml --input-dir data/cards --output-csv data/business-card.csv
```

### 3) Output and resume

```bash
# Output files are in configs/output/
# - *-leads.csv
# - *-report.md
# - *-email-drafts.md

# If interrupted
./run_enrich.sh configs/your-event-name.yaml --resume
```

---

## Output

Each run produces three files in `configs/output/`:

| File | Recipient | Description |
|------|-----------|-------------|
| `{prefix}-leads.csv` | Sales / BD | Cleaned and enriched lead list with `_segment` column; ready for HubSpot import |
| `{prefix}-report.md` | Sales / BD | Segment definitions, follow-up recommendations, compact score tables, and language auto-selected from lead data |
| `{prefix}-email-drafts.md` | Sales / BD | Follow-up email drafts per segment, in the detected language of each recipient (EN / JA / zh-TW) |

---

## Requirements

- Python 3.9 or later
- An OpenAI API key (the tool uses GPT-4o-mini by default)
- macOS or Linux

## LLM provider

The tool uses **GPT-4o-mini** by default — chosen for its low cost per token, which matters when processing hundreds of leads across multiple LLM calls per run.

**Using a different OpenAI-compatible API** (e.g. Azure OpenAI, Groq, Together AI, or a local Ollama instance with an OpenAI-compatible endpoint) requires no code changes. Set two environment variables before running:

```bash
export OPENAI_API_KEY="your-key-for-that-provider"
export OPENAI_BASE_URL="https://your-provider-endpoint/v1"
```

**Using Anthropic or Gemini** requires a small change in `event_leads/enrich.py`. Replace the two client functions near line 213:

```python
# Before (OpenAI)
def _get_client():
    return instructor.from_openai(OpenAI())

def _get_async_client():
    return instructor.from_openai(AsyncOpenAI())

# After (Anthropic example)
from anthropic import Anthropic, AsyncAnthropic
def _get_client():
    return instructor.from_anthropic(Anthropic())

def _get_async_client():
    return instructor.from_anthropic(AsyncAnthropic())
```

You will also need to install the provider's SDK (`pip install anthropic`) and update `requirements.txt` accordingly. The rest of the pipeline does not need to change — `instructor` normalizes the interface across providers.

---

## HubSpot integration

The tool produces a HubSpot-ready CSV. Below is the recommended one-time setup and per-event import process.

### One-time setup in HubSpot

1. In HubSpot, go to **Settings → Properties → Contact properties**
2. Create a new property:
   - **Label:** Lead Segment
   - **Internal name:** `lead_segment`
   - **Field type:** Single-line text (or Dropdown if you want predefined values)
3. Optionally create a second property `Lead Score` (Number type) to import `_score_overall`.

### Per-event import

1. Open `{prefix}-leads.csv` in Excel or Google Sheets
2. Rename the column `_segment` to `Lead Segment` (to match the HubSpot property label), and `_score_overall` to `Lead Score` if you created that property
3. In HubSpot, go to **Contacts → Import → Import a file**
4. Select the CSV; map columns to properties during the import wizard
5. After import, use **Lists** or **Filters** to group contacts by Lead Segment

### Using the email drafts

1. Open `{prefix}-email-drafts.md`
2. Each section is labeled by segment and language
3. In HubSpot, filter contacts by segment, open a contact record, and paste the corresponding draft into the email composer
4. Personalize the `[First Name]` and `[Company]` fields before sending

---

## Configuration reference

### Standard field names

These are the field names the pipeline understands. Map your source column headers to them in the `mapping:` and `survey_fields:` sections.

| Field | Description |
|-------|-------------|
| `name` | Full name |
| `email` | Email address — used as the primary deduplication key |
| `company_title` | Company and title combined in a single column |
| `company` | Company name when it is in its own column |
| `title` | Job title when it is in its own column |
| `phone` | Phone number |
| `interest_scenario` | Stated AI use case interest |
| `demo_interest` | Demo request signal |
| `project_timeline` | Estimated project or evaluation timeline |
| `additional_topics` | Free-text survey responses |

### Scoring dimensions

The LLM scores each lead on four dimensions (0–10). For every dimension, it also provides a one-sentence justification, so each score is transparent and auditable by sales.

The overall score is a weighted average. Weights must sum to 1.0. Descriptions and weights can be adjusted per event in the `scoring:` section of the config.

| Dimension | Default weight | What it measures | Example justification |
|-----------|---------------|-----------------|----------------------|
| `company_fit` | 0.25 | Company size and industry match to target customer profile | "上市金融機構，規模與行業高度符合目標客戶" |
| `seniority_match` | 0.25 | Budget authority or procurement influence | "副總層級，具備採購決策權" |
| `engagement_signal` | 0.35 | On-site interest level and follow-up request | "明確要求安排 demo，專案時程 1 個月內" |
| `interest_alignment` | 0.15 | Alignment between stated interests and core capabilities | "關注流程自動化與知識庫，與 Dify 核心能力直接匹配" |

The report stays compact for large lead volumes. If sales needs detailed score justifications for a specific lead, check the corresponding `_score_*_reason` columns in `{prefix}-leads.csv`.

### Source config options

| Field | Description |
|-------|-------------|
| `file` | Filename relative to `data_dir` |
| `type` | `csv` or `xlsx` |
| `encoding` | Character encoding; use `utf-8-sig` for Windows Excel exports, `big5` for Traditional Chinese exports |
| `mapping` | Maps source column headers to standard field names |
| `survey_fields` | Survey-specific columns; listed separately for LLM context |
| `attendance_status` | `attended` or `registered` |

---

## Troubleshooting

**`OPENAI_API_KEY` not set**

Set the key in your current session and resume:

```bash
export OPENAI_API_KEY="sk-..."
./run_enrich.sh configs/your-event-name.yaml --resume
```

**`UnicodeEncodeError: 'ascii' codec can't encode characters ...`**

Your `OPENAI_API_KEY` or `OPENAI_BASE_URL` likely contains non-ASCII characters (for example smart quotes, full-width symbols, or hidden spaces from copy/paste). Re-export them as plain ASCII:

```bash
unset OPENAI_API_KEY OPENAI_BASE_URL
export OPENAI_API_KEY='sk-...'
# Optional:
# export OPENAI_BASE_URL='https://your-provider-endpoint/v1'
```

**`CONFIG` path does not exist**

Generate the config first, then run:

```bash
python -m event_leads init-config --type event --name "Your Event Name" --date "2026-06-15" --location "Your City"
./run_enrich.sh configs/your-event-name.yaml
```

**Garbled text or encoding errors**

Set `encoding: utf-8-sig` in the source config. For Traditional Chinese exports from Taiwan-based systems, try `big5`.

**Incorrect email language**

Language is inferred from the lead's name characters and email domain: Chinese characters → `zh_tw`, `.jp` domain or Japanese characters → `ja`, all others → `en`. To override, manually edit the `_lang` column in the output CSV.

**Report language is not what you want**

By default, report language is selected from the majority lead language (`en`, `ja`, `zh_tw`). To force one language, set:

```yaml
output:
  filename_prefix: "your-event-name"
  report_language: "en"   # en / ja / zh_tw / auto
```

---

## Project structure

```
event-lead-cli/
├── README.md
├── README-ZH.md
├── run_enrich.sh            ← entry point for each event run
├── requirements.txt
├── data/                    ← source CSV/Excel files
├── configs/
│   ├── event-template.yaml   ← trade show / conference template
│   ├── meetup-template.yaml  ← meetup / community template
│   └── output/
│       ├── checkpoints/     ← intermediate state for --resume (safe to delete after a run)
│       ├── *-leads.csv
│       ├── *-report.md
│       └── *-email-drafts.md
└── event_leads/
    ├── __main__.py
    ├── pipeline.py
    ├── enrich.py
    └── parsers.py
```
