# Event Lead CLI - 操作ガイド

イベントで集めたリードを、スコアリング済み・セグメント済み・メール送信準備済みの成果物に変換する CLI ツールです。  
Google Forms、バッジスキャナー、HubSpot の名刺スキャン CSV など複数ソースを取り込み、正規化・重複排除・LLM スコアリングを行い、営業がすぐ使える 3 つのファイルを出力します。

> English version: [README.md](README.md)  
> 中文版本: [README-ZH.md](README-ZH.md)

---

## CLI の実行手順（実務向け）

### 0) 初回のみ: セットアップ

```bash
git clone https://github.com/saltism/event-lead-cli.git
cd event-lead-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1) イベントごとの通常フロー

```bash
# イベントの CSV/XLSX を data/ に配置

# 設定ファイルを生成（event または meetup）
python -m event_leads init-config --type event --name "Your Event Name" --date "2026-06-15" --location "Singapore"
# python -m event_leads init-config --type meetup --name "Your Meetup Name" --date "2026-06-20" --location "Tokyo"

# OpenAI key を設定
export OPENAI_API_KEY='sk-...'

# 任意: 事前チェック
./scripts/smoke-test.sh configs/your-event-name.yaml

# 実行
./run_enrich.sh configs/your-event-name.yaml
```

### 2) 名刺画像も処理する場合

```bash
# 名刺画像を data/cards/ に配置
python -m event_leads cards-ocr-and-run configs/your-event-name.yaml --input-dir data/cards --output-csv data/business-card.csv
```

### 3) 出力と再開

```bash
# 出力先: configs/output/
# - *-leads.csv
# - *-report.md
# - *-email-drafts.md

# 中断時の再開
./run_enrich.sh configs/your-event-name.yaml --resume
```

---

## 出力ファイル

毎回の実行で `configs/output/` に以下 3 ファイルを生成します。

| ファイル | 利用者 | 内容 |
|------|------|------|
| `{prefix}-leads.csv` | Sales / BD | クレンジング済みリード一覧。`_segment` 列付きで HubSpot へ取り込み可能 |
| `{prefix}-report.md` | Sales / BD | セグメント定義、フォロー方針、コンパクトなスコア表。言語はリード言語から自動選択 |
| `{prefix}-email-drafts.md` | Sales / BD | セグメント別メール草案（受信者の判定言語 EN / JA / zh-TW） |

---

## 要件

- Python 3.9 以上
- OpenAI API key（デフォルトは GPT-4o-mini）
- macOS または Linux

## LLM プロバイダー

デフォルトは **GPT-4o-mini** です。大量リード処理時のコストを抑えるため、このモデルを採用しています。

**OpenAI 互換 API（Azure OpenAI、Groq、Together AI、OpenAI 互換 endpoint の Ollama など）** を使う場合は、コード変更不要です。実行前に以下を設定します。

```bash
export OPENAI_API_KEY="your-key-for-that-provider"
export OPENAI_BASE_URL="https://your-provider-endpoint/v1"
```

**Anthropic / Gemini** を使う場合は、`event_leads/enrich.py` の client 関数を差し替えてください。

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

同時に SDK の追加インストール（`pip install anthropic`）と `requirements.txt` の更新が必要です。パイプライン本体はそのまま使えます。

---

## HubSpot 連携

このツールは HubSpot 取り込み用 CSV を出力します。以下は推奨運用です。

### 初回設定（1 回のみ）

1. HubSpot の **Settings -> Properties -> Contact properties** を開く
2. 新規プロパティを作成:
   - **Label:** Lead Segment
   - **Internal name:** `lead_segment`
   - **Field type:** Single-line text（または Dropdown）
3. 任意で `Lead Score`（Number）を作成し、`_score_overall` を取り込めるようにする

### イベントごとのインポート

1. `{prefix}-leads.csv` を開く
2. `_segment` 列を `Lead Segment` に、`_score_overall` を `Lead Score` に変更（該当プロパティがある場合）
3. HubSpot で **Contacts -> Import -> Import a file**
4. CSV を選択し、列とプロパティをマッピング
5. 取り込み後、Lists / Filters でセグメント別に管理

### メール草案の使い方

1. `{prefix}-email-drafts.md` を開く
2. セクションはセグメントと言語で分かれている
3. HubSpot で対象セグメントを絞り、メール作成画面へ草案を貼り付ける
4. `[First Name]`、`[Company]` を送信前に置換する

---

## 設定リファレンス

### 標準フィールド

`mapping:` と `survey_fields:` で、元データの列名を下記にマップします。

| フィールド | 説明 |
|-------|------|
| `name` | 氏名 |
| `email` | メールアドレス（重複排除の主キー） |
| `company_title` | 会社名と役職が 1 列にある場合 |
| `company` | 会社名（別列の場合） |
| `title` | 役職（別列の場合） |
| `phone` | 電話番号 |
| `interest_scenario` | 想定ユースケースへの関心 |
| `demo_interest` | デモ希望シグナル |
| `project_timeline` | 導入・評価の想定時期 |
| `additional_topics` | 自由記述 |

### スコアリング軸

LLM は 4 軸で 0-10 点を付け、各軸に 1 文の理由を返します。  
総合点は重み付き平均で計算され、重み合計は 1.0 にしてください。重みは `scoring:` でイベントごとに調整できます。

| 軸 | 既定重み | 評価内容 | 理由の例 |
|-----------|---------------|-----------------|----------------------|
| `company_fit` | 0.25 | 企業規模・業界の適合度 | "Public financial company; profile strongly matches the target ICP" |
| `seniority_match` | 0.25 | 予算・調達への意思決定影響 | "VP-level contact with clear purchasing influence" |
| `engagement_signal` | 0.35 | 現地での関心度・フォロー希望 | "Explicitly requested a demo with a near-term timeline" |
| `interest_alignment` | 0.15 | ニーズと製品能力の一致度 | "Needs align with workflow automation and knowledge base use cases" |

大量リード向けにレポートはコンパクト表示です。個別理由を深掘りしたい場合は `{prefix}-leads.csv` の `_score_*_reason` 列を確認してください。

### ソース設定オプション

| フィールド | 説明 |
|-------|------|
| `file` | `data_dir` からの相対ファイル名 |
| `type` | `csv` または `xlsx` |
| `encoding` | 文字コード。Windows Excel は `utf-8-sig`、台湾系エクスポートは `big5` を試す |
| `mapping` | 列名を標準フィールドへ対応付け |
| `survey_fields` | LLM に渡す追加アンケート項目 |
| `attendance_status` | `attended` または `registered` |

---

## troubleshooting

**`OPENAI_API_KEY` が未設定**

```bash
export OPENAI_API_KEY="sk-..."
./run_enrich.sh configs/your-event-name.yaml --resume
```

**`UnicodeEncodeError: 'ascii' codec can't encode characters ...`**

`OPENAI_API_KEY` または `OPENAI_BASE_URL` に全角文字、スマートクオート、不可視スペースが混入している可能性があります。再設定してください。

```bash
unset OPENAI_API_KEY OPENAI_BASE_URL
export OPENAI_API_KEY='sk-...'
# Optional:
# export OPENAI_BASE_URL='https://your-provider-endpoint/v1'
```

**`CONFIG` path does not exist**

先に設定ファイルを生成してください。

```bash
python -m event_leads init-config --type event --name "Your Event Name" --date "2026-06-15" --location "Your City"
./run_enrich.sh configs/your-event-name.yaml
```

**`zsh: command not found: #`**

コメント行（`#` で始まる行）まで貼り付けた状態です。実行コマンドのみ貼り付けてください。

**`Error: Got unexpected extra argument (...)`**

`run_enrich.sh` には設定ファイルを 1 つだけ渡します。

```bash
./run_enrich.sh configs/your-event-name.yaml
```

**`--resume` 後に全スコアが 0**

失敗した過去実行の checkpoint を再利用している可能性があります。checkpoint を削除して再実行します。

```bash
rm -f configs/output/checkpoints/*.pkl
./run_enrich.sh configs/your-event-name.yaml
```

**セグメントが A/B/C 固定にならない**

旧バージョンで生成した成果物が混在している可能性があります。最新コードに更新し、古いレポートを削除して再実行してください。

```bash
git pull
rm -f configs/output/*-report.md configs/output/*-leads.csv
./run_enrich.sh configs/your-event-name.yaml
```

**最初の LLM 呼び出しが遅い**

初回リクエストはモデルのウォームアップやネットワーク遅延の影響で遅くなることがあります。セグメント処理の不具合ではありません。長時間実行では `--resume` を使って完了済みステージの再計算を避けてください。

**文字化け・エンコード異常**

`encoding: utf-8-sig` を設定してください。台湾系システムの出力は `big5` が有効な場合があります。

**メール言語判定が期待と違う**

判定は以下です: 中国語文字 -> `zh_tw`、`.jp` ドメインまたは日本語文字 -> `ja`、それ以外 -> `en`。必要なら出力 CSV の `_lang` を手動修正してください。

**レポート言語を固定したい**

```yaml
output:
  filename_prefix: "your-event-name"
  report_language: "en"   # en / ja / zh_tw / auto
```

---

## プロジェクト構成

```
event-lead-cli/
├── README.md
├── README-ZH.md
├── README-JA.md
├── run_enrich.sh            ← 実行エントリ
├── requirements.txt
├── data/                    ← 入力データ（CSV/Excel）
├── configs/
│   ├── event-template.yaml   ← 展示会 / conference テンプレート
│   ├── meetup-template.yaml  ← meetup / コミュニティ用テンプレート
│   └── output/
│       ├── checkpoints/     ← --resume 用の中間ファイル
│       ├── *-leads.csv
│       ├── *-report.md
│       └── *-email-drafts.md
└── event_leads/
    ├── __main__.py
    ├── pipeline.py
    ├── enrich.py
    └── parsers.py
```
