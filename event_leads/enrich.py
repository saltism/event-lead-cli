"""LLM enrichment and segmentation using Instructor + OpenAI."""

import asyncio
import os
from typing import List, Optional

import instructor
import pandas as pd
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Default scoring dimensions (overridden by config YAML if present)
# ---------------------------------------------------------------------------

DEFAULT_SCORING_DIMS = [
    {
        'id': 'company_fit',
        'label': '公司匹配度',
        'description': (
            '公司規模和行業是否符合 Dify 的目標客戶（需要 AI 工具的中大型企業，有 IT 部門）。'
            '上市公司、金融、製造、科技、醫療等行業加分；小微企業或個人創業者低分。'
        ),
        'weight': 0.25,
    },
    {
        'id': 'seniority_match',
        'label': '職級決策力',
        'description': (
            '職位是否具備預算決策或採購影響力。'
            'CIO / CTO / CISO / 資訊長 / 副總 / 總經理 = 高分；'
            '經理 / 主任 = 中分；工程師 / 職位不明 = 低分。'
        ),
        'weight': 0.25,
    },
    {
        'id': 'engagement_signal',
        'label': '互動意圖',
        'description': (
            '現場表態的積極程度。'
            '「是，請聯繫我安排」= 最高；「先發資料看看」+ 時程 1-3 個月 = 中高；'
            '「先發資料看看」+ 無計畫 = 中；「暫不需要」= 低。'
        ),
        'weight': 0.35,
    },
    {
        'id': 'interest_alignment',
        'label': '需求契合度',
        'description': (
            '興趣方向是否與 Dify 的核心能力直接匹配。'
            '流程自動化 / 工作流、內部知識庫 / 智能客服、AI Agent = 高分；'
            '數據分析、銷售管理 = 中分；其他 = 較低分。'
        ),
        'weight': 0.15,
    },
]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class LeadScores(BaseModel):
    """Flat scoring model — 4 fixed dimensions, each 0-10, each with a reason.
    Flat dict format because gpt-4o-mini reliably produces it without retries."""
    company_fit: int = Field(default=0, ge=0, le=10,
        description="How well the company matches Dify's ICP (0-10)")
    company_fit_reason: str = Field(default="",
        description="One short sentence justifying the company_fit score")
    seniority_match: int = Field(default=0, ge=0, le=10,
        description="Whether the person has budget decision power (0-10)")
    seniority_match_reason: str = Field(default="",
        description="One short sentence justifying the seniority_match score")
    engagement_signal: int = Field(default=0, ge=0, le=10,
        description="How actively they engaged: demo requested, clear timeline (0-10)")
    engagement_signal_reason: str = Field(default="",
        description="One short sentence justifying the engagement_signal score")
    interest_alignment: int = Field(default=0, ge=0, le=10,
        description="How closely their interests match Dify's core capabilities (0-10)")
    interest_alignment_reason: str = Field(default="",
        description="One short sentence justifying the interest_alignment score")


class LeadEnrichmentItem(BaseModel):
    email: str = Field(description="The lead's email (used to match back)")
    interest_tags: List[str] = Field(
        description="2-5 standardized interest tags. Use: workflow_automation, "
        "knowledge_base, chatbot, data_analytics, code_assistant, content_generation, sales_crm"
    )
    seniority_level: str = Field(description="One of: C-level, VP, Director, Manager, IC, Unknown")
    suggested_angle: str = Field(
        description="One sentence: the best follow-up angle for sales (Traditional Chinese)"
    )
    scores: LeadScores


class BatchEnrichment(BaseModel):
    leads: List[LeadEnrichmentItem]


class SegmentDef(BaseModel):
    segment_id: str = Field(description="Short ID like A, B, C, D")
    name: str = Field(description="Segment name in Traditional Chinese, e.g. 高意向決策者")
    count: int
    score_range: str = Field(description="Typical overall score range for this segment, e.g. '7-10'")
    characteristics: List[str] = Field(description="2-4 bullet points describing this segment")
    recommended_action: str = Field(description="What sales should do (Traditional Chinese)")
    hubspot_suggestion: str = Field(description="How to set up HubSpot for this segment (Traditional Chinese)")


class SegmentAssignment(BaseModel):
    email: str
    segment_id: str


class SegmentReport(BaseModel):
    overview: str = Field(description="2-3 sentence executive summary in Traditional Chinese")
    segments: List[SegmentDef]
    assignments: List[SegmentAssignment]


class SegmentThreshold(BaseModel):
    """Score-range definition for one segment; used to map leads locally."""
    segment_id: str = Field(description="Short ID: A, B, C, or D (A = highest)")
    name: str = Field(description="Segment name in Traditional Chinese, e.g. 高意向決策者")
    min_score: float = Field(description="Minimum overall_score for this segment (inclusive)")
    max_score: float = Field(description="Maximum overall_score for this segment (inclusive); use 10.0 for the top segment")
    characteristics: List[str] = Field(description="2-3 bullets describing who belongs here")
    recommended_action: str = Field(description="What sales should do next (Traditional Chinese)")
    hubspot_suggestion: str = Field(description="HubSpot setup suggestion for this segment (Traditional Chinese)")


class SegmentThresholds(BaseModel):
    """Output of the single threshold-definition LLM call."""
    thresholds: List[SegmentThreshold] = Field(
        description="Exactly 3 segments (A/B/C) ordered from highest to lowest min_score; together they must cover 0–10 with no gaps"
    )
    overview: str = Field(description="One-sentence executive summary of the lead pool in Traditional Chinese")


class SingleEmailDraft(BaseModel):
    subject: str = Field(description="Email subject line, under 50 characters")
    body: str = Field(description="Email body, 100-200 words")


# ---------------------------------------------------------------------------
# Prompts & brand constants
# ---------------------------------------------------------------------------

DIFY_PRODUCT_BRIEF = """\
Dify is an AI application development platform (B2B SaaS) by LangGenius.
Core capabilities: visual Workflow builder, Knowledge Base (RAG), AI Agent orchestration, prompt management.
Dify helps enterprise teams build and deploy AI-powered applications without deep ML expertise.

Brand rules:
- Do NOT say "open-source" or highlight GitHub stars.
- Do NOT pile company stats (funding, deployment numbers) in outreach emails.
- Legal entity names: English → LangGenius KK | Japanese → 株式会社LangGenius（Dify）| Traditional Chinese → Dify（株式会社LangGenius）
- Never use "Fifty" or any variant as company/product name.
- If legal entity is not needed, use product name "Dify" only.
"""

DIFY_CAPABILITY_MAP = """\
Dify capability mapping for common interests:
- 流程自動化/工作流 → Dify Workflow builder: visual drag-and-drop automation for business processes
- 內部知識庫/智能客服 → Dify Knowledge Base + Chatbot: RAG-powered knowledge search and Q&A
- 數據分析/報表 → Dify orchestrates data analysis workflows with LLM-powered insights
- 程式碼輔助/開發提效 → Dify Agent: AI coding assistants and developer productivity tools
- 銷售與客戶管理 → Dify Workflow: automate CRM tasks, lead scoring, customer communication
"""

SEGMENT_SYSTEM_PROMPT = """\
You are an expert B2B lead analyst. Given enriched event leads (including dimension scores),
create 3-5 segments and assign EVERY lead to exactly one segment.

CRITICAL RULES:
- 'assignments' MUST contain exactly one entry per lead (match by email).
- Every email in the input MUST appear in assignments. Do NOT skip any.
- segment_id in each assignment MUST match one of your defined segments.
- 'count' in each segment MUST equal the number of leads you assign to it.
- Use the overall_score and dimension scores to draw clear boundaries between segments.
- Include a score_range for each segment showing typical score range (e.g. "7-10").

Write descriptions, recommended_action, and hubspot_suggestion in Traditional Chinese.
Cite score patterns and counts to justify each segment.
"""

THRESHOLD_SYSTEM_PROMPT = """\
You are a B2B sales strategist analyzing event leads for Dify.

You will receive the distribution of overall_score values (0–10) for a set of leads.
Your job is to define EXACTLY 3 segments with clear score thresholds: A (highest), B (middle), C (lowest).

Rules:
- Return exactly 3 threshold objects, with segment_id fixed to A, B, C.
- thresholds must cover the FULL range 0–10 with no gaps.
  A.max_score must be 10.0; C.min_score must be 0.0.
- Order must be A first, then B, then C.
- Base boundaries on natural breaks in the provided score list, not on fixed quartiles.
- recommended_action and hubspot_suggestion must be actionable and specific.
- Do NOT assign individual leads — only define the thresholds.
"""

LANG_LABELS = {
    'zh_tw': '繁體中文（台灣）',
    'en': 'English',
    'ja': '日本語',
}

LANG_INSTRUCTIONS = {
    'zh_tw': 'Traditional Chinese (Taiwan) — formal business tone (繁體中文，敬語)',
    'en': 'English — professional and warm',
    'ja': 'Japanese — 敬語 (keigo), business formal',
}

REPORT_LANGUAGE_INSTRUCTIONS = {
    'en': 'English',
    'ja': 'Japanese',
    'zh_tw': 'Traditional Chinese (Taiwan)',
}

REPORT_COPY = {
    'en': {
        'title_suffix': 'Lead processing report',
        'overview': 'Overview',
        'total_leads': 'Total leads',
        'scoring_method': 'Scoring method',
        'scoring_intro_1': 'Each lead is scored on the dimensions below (0-10), and the overall score is a weighted average.',
        'scoring_intro_2': 'Detailed per-dimension reasons are available in the CSV columns: `_score_*_reason`.',
        'dim_col': 'Dimension',
        'weight_col': 'Weight',
        'criteria_col': 'Criteria',
        'scores_overview': 'Lead score overview',
        'name': 'Name',
        'company': 'Company',
        'title': 'Title',
        'email': 'Email',
        'overall': 'Overall',
        'segment': 'Segment',
        'demo': 'Demo',
        'timeline': 'Timeline',
        'features': 'Characteristics',
        'action': 'Recommended action',
        'hubspot': 'HubSpot operation',
        'reason_note': 'Detailed scoring reasons are in CSV columns: `_score_*_reason`.',
    },
    'ja': {
        'title_suffix': 'リード処理レポート',
        'overview': '概要',
        'total_leads': '総リード数',
        'scoring_method': 'スコアリング方法',
        'scoring_intro_1': '各リードを以下の指標で 0-10 点評価し、総合点は重み付き平均で計算します。',
        'scoring_intro_2': '各指標の詳細理由は CSV の `_score_*_reason` 列を参照してください。',
        'dim_col': '指標',
        'weight_col': '重み',
        'criteria_col': '評価基準',
        'scores_overview': 'リードスコア一覧',
        'name': '氏名',
        'company': '会社',
        'title': '役職',
        'email': 'メール',
        'overall': '総合',
        'segment': 'セグメント',
        'demo': 'デモ',
        'timeline': '時期',
        'features': '特徴',
        'action': '推奨アクション',
        'hubspot': 'HubSpot 操作',
        'reason_note': '詳細な採点理由は CSV の `_score_*_reason` 列にあります。',
    },
    'zh_tw': {
        'title_suffix': '線索處理報告',
        'overview': '概覽',
        'total_leads': '總線索',
        'scoring_method': '評分方法說明',
        'scoring_intro_1': '每條線索依據以下維度評分（0-10 分），綜合分為加權平均。',
        'scoring_intro_2': '逐維度評分依據可在 CSV 的 `_score_*_reason` 欄位查看。',
        'dim_col': '維度',
        'weight_col': '權重',
        'criteria_col': '評分標準',
        'scores_overview': '線索評分總覽',
        'name': '姓名',
        'company': '公司',
        'title': '職位',
        'email': 'Email',
        'overall': '綜合',
        'segment': 'Segment',
        'demo': 'Demo',
        'timeline': '時程',
        'features': '特徵',
        'action': '建議動作',
        'hubspot': 'HubSpot 操作',
        'reason_note': '各維度評分依據詳見 CSV 的 `_score_*_reason` 欄位。',
    },
}

FALLBACK_SEGMENTS = {
    'en': [
        ("A", "High-intent decision makers", "Prioritize direct outreach, schedule demo within 7 days."),
        ("B", "Medium-intent evaluators", "Send tailored materials and follow up within 14 days."),
        ("C", "Low-intent or early-stage", "Nurture with resources and re-engage next cycle."),
    ],
    'ja': [
        ("A", "高意向の意思決定層", "優先連絡し、7日以内にデモ日程を設定。"),
        ("B", "中意向の検討層", "資料提供後、14日以内に再フォロー。"),
        ("C", "低意向・情報収集層", "ナーチャリング中心で次回接点を設計。"),
    ],
    'zh_tw': [
        ("A", "高意向決策者", "優先聯繫，7 天內安排 demo。"),
        ("B", "中意向評估中", "提供對應資料，14 天內再次跟進。"),
        ("C", "低意向或早期探索", "以內容養成為主，安排下次觸達。"),
    ],
}


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def detect_language(row) -> str:
    """Return 'zh_tw', 'ja', or 'en' based on name/email/company signals."""
    name = str(row.get('name', ''))
    email = str(row.get('email', ''))
    company = str(row.get('company', ''))

    if email.lower().endswith('.jp'):
        return 'ja'
    for ch in name + company:
        if '\u3040' <= ch <= '\u309f' or '\u30a0' <= ch <= '\u30ff':
            return 'ja'

    if '.tw' in email.lower():
        return 'zh_tw'
    for ch in name:
        if '\u4e00' <= ch <= '\u9fff':
            return 'zh_tw'

    return 'en'


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_client():
    return instructor.from_openai(OpenAI(**_openai_client_kwargs()))


def _get_async_client():
    return instructor.from_openai(AsyncOpenAI(**_openai_client_kwargs()))


def _openai_client_kwargs() -> dict:
    """Load OpenAI settings from env and fail fast on malformed values.

    This avoids obscure httpx UnicodeEncodeError when a copied key/base URL
    contains full-width punctuation or invisible unicode whitespace.
    """
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    base_url = os.getenv('OPENAI_BASE_URL', '').strip()

    if not api_key:
        raise ValueError('OPENAI_API_KEY is missing. Please export a valid API key.')
    if any(ord(ch) > 127 for ch in api_key):
        raise ValueError('OPENAI_API_KEY contains non-ASCII characters. Re-copy it as plain text.')
    if base_url and any(ord(ch) > 127 for ch in base_url):
        raise ValueError('OPENAI_BASE_URL contains non-ASCII characters. Re-copy it as plain text.')

    kwargs = {'api_key': api_key}
    if base_url:
        kwargs['base_url'] = base_url
    return kwargs


def _get_dims(config) -> list:
    """Return scoring dimensions from config, or defaults."""
    if config:
        dims = config.get('scoring', {}).get('dimensions', [])
        if dims:
            return dims
    return DEFAULT_SCORING_DIMS


def _compute_overall(scores: LeadScores, dims: list) -> float:
    """Compute weighted overall score in Python (don't trust LLM arithmetic).
    scores is now a flat LeadScores object; read each dimension by its id."""
    weight_map = {d['id']: float(d.get('weight', 1.0)) for d in dims}
    total_w = sum(weight_map.values()) or 1.0
    total = sum(
        getattr(scores, dim_id, 0) * w
        for dim_id, w in weight_map.items()
    )
    return round(total / total_w, 1)


def _select_report_language(df: pd.DataFrame, config: Optional[dict]) -> str:
    """Pick report language by config override or lead-language majority."""
    output_cfg = (config or {}).get('output', {})
    forced = str(output_cfg.get('report_language', 'auto')).strip().lower()
    if forced in ('en', 'ja', 'zh_tw'):
        return forced

    lang_counts = df['_lang'].value_counts()
    if lang_counts.empty:
        return 'en'

    top_lang = str(lang_counts.index[0])
    return top_lang if top_lang in REPORT_COPY else 'en'


def _format_leads(leads: list) -> str:
    lines = []
    for i, lead in enumerate(leads, 1):
        parts = [f"{k}: {v}" for k, v in lead.items() if v]
        lines.append(f"[{i}] {' | '.join(parts)}")
    return '\n'.join(lines)


def _build_enrich_prompt(dims: list) -> str:
    dim_lines = '\n'.join(
        f"  - {d['id']} (0-10): {d['description']}" for d in dims
    )
    return f"""\
You are an expert B2B event lead analyst for Dify.

{DIFY_PRODUCT_BRIEF}

Enrich each lead with:
1. interest_tags: 2-5 standardized tags from survey answers
2. seniority_level: C-level / VP / Director / Manager / IC / Unknown
3. suggested_angle: one-sentence follow-up angle in Traditional Chinese
4. scores: score each lead on each dimension with an integer 0-10, and provide a short reason (one sentence in Traditional Chinese) for each score:
{dim_lines}
   For each dimension, return both the score AND a reason field (e.g. company_fit + company_fit_reason).

Context: These leads are from a CIO/CTO forum in Taiwan — enterprise IT decision makers.
Be objective. Use only the data provided; do not guess missing information.
"""


# ---------------------------------------------------------------------------
# Stage 3: Enrich (with scoring)
# ---------------------------------------------------------------------------

async def _enrich_batch_async(
    batch: list,
    batch_idx: int,
    n_batches: int,
    system_prompt: str,
    semaphore: asyncio.Semaphore,
) -> list:
    """Call the LLM for one batch of leads (async, rate-limited by semaphore)."""
    async with semaphore:
        print(f"  Enriching batch {batch_idx}/{n_batches} ({len(batch)} leads)...")
        client = _get_async_client()
        result = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_model=BatchEnrichment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Enrich these leads:\n{_format_leads(batch)}"},
            ],
            max_retries=2,
        )
        return result.leads


async def _enrich_all_async(
    leads_data: list,
    batch_size: int,
    system_prompt: str,
    concurrency: int = 10,
) -> list:
    """Fan out all batches concurrently (max `concurrency` at a time)."""
    semaphore = asyncio.Semaphore(concurrency)
    batches = [leads_data[i:i + batch_size] for i in range(0, len(leads_data), batch_size)]
    n_batches = len(batches)

    tasks = [
        _enrich_batch_async(batch, idx + 1, n_batches, system_prompt, semaphore)
        for idx, batch in enumerate(batches)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for idx, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"  [warning] batch {idx + 1} failed: {r}")
        else:
            all_items.extend(r)
    return all_items


def enrich_leads(df: pd.DataFrame, batch_size: int = 8, config: Optional[dict] = None) -> pd.DataFrame:
    """Add LLM enrichment columns + dimension scores to the DataFrame.

    Batches are sent concurrently (up to 10 at a time) for speed.
    For 800 leads at batch_size=8 → 100 batches → ~10x faster than sequential.
    """
    dims = _get_dims(config)
    system_prompt = _build_enrich_prompt(dims)

    leads_data = []
    for _, row in df.iterrows():
        leads_data.append({
            'email': row.get('email', ''),
            'name': row.get('name', ''),
            'company': row.get('company', ''),
            'title': row.get('title', ''),
            'interest_scenario': row.get('interest_scenario', ''),
            'demo_interest': row.get('demo_interest', ''),
            'project_timeline': row.get('project_timeline', ''),
            'additional_topics': row.get('additional_topics', ''),
        })

    n_batches = (len(leads_data) + batch_size - 1) // batch_size
    print(f"  {len(leads_data)} leads → {n_batches} batches (concurrency=10)")
    all_enrichments = asyncio.run(_enrich_all_async(leads_data, batch_size, system_prompt))
    if leads_data and not all_enrichments:
        raise RuntimeError(
            "LLM enrichment returned 0 results. Check OPENAI_API_KEY/OPENAI_BASE_URL and retry. "
            "No checkpoint was saved for enrich stage."
        )

    enrichment_map = {e.email.lower(): e for e in all_enrichments}

    df = df.copy()
    df['interest_tags'] = ''
    df['seniority_level'] = ''
    df['suggested_angle'] = ''
    df['_score_overall'] = 0.0
    for d in dims:
        df[f'_score_{d["id"]}'] = 0
        df[f'_score_{d["id"]}_reason'] = ''

    for idx, row in df.iterrows():
        email = str(row.get('email', '')).lower()
        if email not in enrichment_map:
            continue
        e = enrichment_map[email]
        df.at[idx, 'interest_tags'] = ', '.join(e.interest_tags)
        df.at[idx, 'seniority_level'] = e.seniority_level
        df.at[idx, 'suggested_angle'] = e.suggested_angle

        overall = _compute_overall(e.scores, dims)
        df.at[idx, '_score_overall'] = overall

        for d in dims:
            dim_id = d['id']
            score_col = f'_score_{dim_id}'
            reason_col = f'_score_{dim_id}_reason'
            if score_col in df.columns:
                df.at[idx, score_col] = getattr(e.scores, dim_id, 0)
            if reason_col in df.columns:
                df.at[idx, reason_col] = getattr(e.scores, f'{dim_id}_reason', '')

    return df


# ---------------------------------------------------------------------------
# Stage 4: Segment report
# ---------------------------------------------------------------------------

def _map_to_segments(df: pd.DataFrame, thresholds: list) -> 'pd.Series':
    """Assign each lead a segment_id based on its _score_overall and the threshold list.

    Thresholds are sorted highest-first; the first threshold whose min_score
    is <= the lead's score wins.  Falls back to the lowest segment.
    """
    sorted_t = sorted(thresholds, key=lambda t: t.min_score, reverse=True)
    fallback = sorted_t[-1].segment_id

    def _assign(score):
        for t in sorted_t:
            if score >= t.min_score:
                return t.segment_id
        return fallback

    return df['_score_overall'].apply(_assign)


def _fallback_three_thresholds(scores: list, report_lang: str) -> SegmentThresholds:
    """Deterministic fallback when LLM thresholds are malformed."""
    s = pd.Series(scores, dtype=float)
    q67 = float(s.quantile(0.67))
    q33 = float(s.quantile(0.33))
    a_min = max(0.0, min(10.0, round(q67, 1)))
    b_min = max(0.0, min(a_min, round(q33, 1)))
    c_min = 0.0
    b_max = round(max(b_min, a_min - 0.1), 1)
    c_max = round(max(c_min, b_min - 0.1), 1)

    defaults = FALLBACK_SEGMENTS.get(report_lang, FALLBACK_SEGMENTS['en'])
    thresholds = [
        SegmentThreshold(
            segment_id=defaults[0][0], name=defaults[0][1], min_score=a_min, max_score=10.0,
            characteristics=["Top score band", "Clear near-term potential"],
            recommended_action=defaults[0][2],
            hubspot_suggestion="Create high-priority list and immediate follow-up task.",
        ),
        SegmentThreshold(
            segment_id=defaults[1][0], name=defaults[1][1], min_score=b_min, max_score=b_max,
            characteristics=["Mid score band", "Needs qualification"],
            recommended_action=defaults[1][2],
            hubspot_suggestion="Use nurture sequence and schedule second touchpoint.",
        ),
        SegmentThreshold(
            segment_id=defaults[2][0], name=defaults[2][1], min_score=c_min, max_score=c_max,
            characteristics=["Lower score band", "Longer cycle or weaker signal"],
            recommended_action=defaults[2][2],
            hubspot_suggestion="Tag for long-term nurture and periodic re-engagement.",
        ),
    ]
    return SegmentThresholds(thresholds=thresholds, overview="Fallback thresholds were applied due to malformed model output.")


def _normalize_thresholds(thresholds_result: SegmentThresholds, scores: list, report_lang: str) -> SegmentThresholds:
    """Force exactly three ordered segments A/B/C; fallback if needed."""
    sorted_t = sorted(thresholds_result.thresholds, key=lambda x: x.min_score, reverse=True)
    if len(sorted_t) != 3:
        return _fallback_three_thresholds(scores, report_lang)

    ids = [t.segment_id for t in sorted_t]
    if ids != ['A', 'B', 'C']:
        return _fallback_three_thresholds(scores, report_lang)

    # Ensure full coverage and monotonic order.
    sorted_t[0].max_score = 10.0
    sorted_t[2].min_score = 0.0
    if not (sorted_t[0].min_score >= sorted_t[1].min_score >= sorted_t[2].min_score):
        return _fallback_three_thresholds(scores, report_lang)
    return SegmentThresholds(thresholds=sorted_t, overview=thresholds_result.overview)


def _build_report_from_thresholds(
    thresholds_result: 'SegmentThresholds', df: pd.DataFrame
) -> SegmentReport:
    """Assemble a SegmentReport object from threshold definitions and the mapped df.

    Segments with zero actual members are omitted so downstream email generation
    doesn't produce empty drafts.
    """
    segments = []
    assignments = []

    for t in sorted(thresholds_result.thresholds, key=lambda x: x.min_score, reverse=True):
        members = df[df['_segment'] == t.segment_id]
        if members.empty:
            continue
        segments.append(SegmentDef(
            segment_id=t.segment_id,
            name=t.name,
            count=len(members),
            score_range=f"{t.min_score:.1f}–{t.max_score:.1f}",
            characteristics=t.characteristics,
            recommended_action=t.recommended_action,
            hubspot_suggestion=t.hubspot_suggestion,
        ))
        for _, row in members.iterrows():
            assignments.append(SegmentAssignment(
                email=str(row.get('email', '')),
                segment_id=t.segment_id,
            ))

    return SegmentReport(
        overview=thresholds_result.overview,
        segments=segments,
        assignments=assignments,
    )


def generate_segment_report(df: pd.DataFrame, config: Optional[dict] = None) -> tuple:
    """Generate segment assignments and a Markdown report.

    Strategy (fast path):
      1. Compute score statistics locally — 0 ms.
      2. One small LLM call: given the score distribution, define 3–4 segments
         with explicit score thresholds.  No per-lead assignments in the output.
      3. Map every lead to a segment in pure Python — 0 ms.
      4. Build SegmentReport object and render Markdown.

    Returns (updated_df, markdown_report, SegmentReport_object).
    """
    client = _get_client()
    dims = _get_dims(config)
    df = df.copy()
    event = (config or {}).get('event', {})
    if '_event_name' not in df.columns:
        df['_event_name'] = event.get('name', 'Event')
    if '_event_date' not in df.columns:
        df['_event_date'] = event.get('date', '')
    df['_lang'] = df.apply(detect_language, axis=1)
    report_lang = _select_report_language(df, config)
    report_lang_instruction = REPORT_LANGUAGE_INSTRUCTIONS.get(report_lang, 'English')

    scores = sorted(df['_score_overall'].tolist(), reverse=True)
    score_stats = (
        f"Count: {len(scores)}\n"
        f"Range: {min(scores):.1f}–{max(scores):.1f}  "
        f"Mean: {sum(scores)/len(scores):.1f}\n"
        f"Scores (high→low): {[round(s, 1) for s in scores]}"
    )

    print("  Defining segment thresholds (1 LLM call)...")
    thresholds_result = client.chat.completions.create(
        model="gpt-4o-mini",
        response_model=SegmentThresholds,
        messages=[
            {"role": "system", "content": THRESHOLD_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Lead pool score distribution:\n{score_stats}\n\n"
                f"Define exactly 3 segments (A/B/C) with score thresholds covering 0–10.\n"
                f"Write all output fields in: {report_lang_instruction}."
            )},
        ],
        max_retries=2,
    )
    thresholds_result = _normalize_thresholds(thresholds_result, scores, report_lang)

    df['_segment'] = _map_to_segments(df, thresholds_result.thresholds)

    report = _build_report_from_thresholds(thresholds_result, df)
    md = _render_report_markdown(report, df, dims, report_lang)
    return df, md, report


# ---------------------------------------------------------------------------
# Stage 5: Email drafts (one LLM call per segment × language pair)
# ---------------------------------------------------------------------------

def generate_email_drafts(report: SegmentReport, df: pd.DataFrame, config: dict) -> str:
    """Generate per-segment email drafts; one call per (segment, language) pair."""
    client = _get_client()
    event = config.get('event', {})
    event_name = event.get('name', 'Event')
    event_date = event.get('date', '')
    event_location = event.get('location', '')

    seg_names = {s.segment_id: s.name for s in report.segments}
    seg_actions = {s.segment_id: s.recommended_action for s in report.segments}

    # Collect all (segment, language, members) triples
    pairs = []
    for seg in report.segments:
        seg_members = df[df['_segment'] == seg.segment_id]
        if seg_members.empty:
            continue
        for lang in sorted(seg_members['_lang'].unique()):
            lang_members = seg_members[seg_members['_lang'] == lang]
            pairs.append((seg.segment_id, lang, lang_members))

    # Generate one email per pair
    all_drafts = []
    total = len(pairs)
    for i, (sid, lang, lang_members) in enumerate(pairs, 1):
        print(f"  Draft {i}/{total}: Segment {sid} × {lang} ({len(lang_members)} leads)...")
        top_interests = lang_members['interest_scenario'].value_counts().head(3).index.tolist()
        avg_score = lang_members['_score_overall'].mean() if '_score_overall' in lang_members else None

        score_context = f" (avg overall score: {avg_score:.1f}/10)" if avg_score is not None else ""
        action = seg_actions.get(sid, '')

        prompt = f"""\
Write ONE follow-up email for this specific group:
- Segment: {sid} "{seg_names.get(sid, sid)}"{score_context}
- Language: {LANG_INSTRUCTIONS[lang]}
- Recipients ({len(lang_members)} people): {', '.join(str(r.get('name', r.get('email', ''))) for _, r in lang_members.iterrows())}
- Top interests: {', '.join(top_interests)}
- Sales action for this segment: {action}

Event: {event_name} ({event_date}, {event_location}) — first follow-up after the event.

{DIFY_PRODUCT_BRIEF}
{DIFY_CAPABILITY_MAP}
Rules:
- Use HubSpot tokens: {{{{contact.firstname}}}}, {{{{contact.company}}}}
- Subject: under 50 characters
- Body: 100-200 words, short paragraphs
- Reference the event at the start
- CTA: [DEMO_LINK] for demo booking, [RESOURCE_LINK] for resources
- High-intent segment (demo requested) → ask directly for demo; low-intent → share resources only
- Do NOT mention "open-source" or GitHub stars
- Brand name guardrail:
  - Never output "Fifty" (or variants) as brand/entity.
  - Use only these legal-entity forms when needed:
    - English: LangGenius KK
    - Japanese: 株式会社LangGenius（Dify） or Dify（株式会社LangGenius）
    - Traditional Chinese: Dify（株式会社LangGenius）
"""

        draft = client.chat.completions.create(
            model="gpt-4o-mini",
            response_model=SingleEmailDraft,
            messages=[
                {"role": "system", "content": "You are an expert B2B email copywriter fluent in Traditional Chinese, English, and Japanese."},
                {"role": "user", "content": prompt},
            ],
            max_retries=2,
        )
        all_drafts.append((sid, lang, draft, lang_members))

    return _render_email_drafts_markdown(all_drafts, report, df)


# ---------------------------------------------------------------------------
# Report renderers
# ---------------------------------------------------------------------------

def _render_report_markdown(
    report: SegmentReport, df: pd.DataFrame, dims: list, report_lang: str = 'en'
) -> str:
    copy = REPORT_COPY.get(report_lang, REPORT_COPY['en'])
    event_name = df['_event_name'].iloc[0] if '_event_name' in df.columns else 'Event'
    event_date = df['_event_date'].iloc[0] if '_event_date' in df.columns else ''

    dim_table = '\n'.join(
        f"| {d['label']} | {d.get('weight', 0)} | {d['description'].strip()} |"
        for d in dims
    )
    lines = [
        f"# {event_name} — {copy['title_suffix']}",
        "",
        f"> Generated by Event Lead CLI v0.5 | {event_date}",
        "",
        f"## {copy['overview']}",
        "",
        f"- {copy['total_leads']}: **{len(df)}**",
        f"- {report.overview}",
        "",
        f"## {copy['scoring_method']}",
        "",
        copy['scoring_intro_1'],
        copy['scoring_intro_2'],
        "",
        f"| {copy['dim_col']} | {copy['weight_col']} | {copy['criteria_col']} |",
        "|------|------|----------|",
        dim_table,
        "",
    ]

    # Scores overview table (sorted by overall score desc)
    if '_score_overall' in df.columns:
        lines.append(f"## {copy['scores_overview']}")
        lines.append("")
        dim_headers = ' | '.join(d['label'] for d in dims)
        dim_seps = ' | '.join('----' for _ in dims)
        lines.append(
            f"| {copy['name']} | {copy['company']} | {dim_headers} | {copy['overall']} | {copy['segment']} |"
        )
        lines.append(f"|------|------|{dim_seps}|--------|---------|")
        sorted_df = df.sort_values('_score_overall', ascending=False)
        for _, row in sorted_df.iterrows():
            name = row.get('name', '')
            comp = str(row.get('company', '')) if pd.notna(row.get('company')) else ''
            dim_scores = ' | '.join(str(int(row.get(f'_score_{d["id"]}', 0))) for d in dims)
            overall = row.get('_score_overall', 0)
            seg = row.get('_segment', '')
            lines.append(f"| {name} | {comp} | {dim_scores} | **{overall}** | {seg} |")
        lines.append("")

    # Per-segment sections
    for seg in report.segments:
        members = df[df['_segment'] == seg.segment_id]
        actual_count = len(members)
        if actual_count == 0:
            continue

        avg = members['_score_overall'].mean() if '_score_overall' in members.columns else None
        avg_str = f", avg {avg:.1f}" if avg is not None else ""
        lines.append(
            f"## Segment {seg.segment_id}: {seg.name} ({actual_count}{avg_str}, range {seg.score_range})"
        )
        lines.append("")
        lines.append(f"**{copy['features']}:**")
        for c in seg.characteristics:
            lines.append(f"- {c}")
        lines.append("")
        lines.append(f"**{copy['action']}:** {seg.recommended_action}")
        lines.append("")
        lines.append(f"**{copy['hubspot']}:** {seg.hubspot_suggestion}")
        lines.append("")

        # Member table (compact: one row per lead, scores only; reasons in CSV)
        dim_headers = ' | '.join(d['label'] for d in dims)
        dim_seps = ' | '.join('----' for _ in dims)
        lines.append(
            f"| {copy['name']} | {copy['company']} | {copy['title']} | {copy['email']} | "
            f"{dim_headers} | {copy['overall']} | {copy['demo']} | {copy['timeline']} |"
        )
        lines.append(f"|------|------|------|-------|{dim_seps}|------|------|------|")
        for _, m in members.sort_values('_score_overall', ascending=False).iterrows():
            name = m.get('name', '')
            comp = str(m.get('company', '')) if pd.notna(m.get('company')) else ''
            title = str(m.get('title', '')) if pd.notna(m.get('title')) else ''
            email = m.get('email', '')
            dim_scores = ' | '.join(str(int(m.get(f'_score_{d["id"]}', 0))) for d in dims)
            overall = m.get('_score_overall', 0)
            demo = m.get('demo_interest', '')
            timeline = m.get('project_timeline', '')
            lines.append(f"| {name} | {comp} | {title} | {email} | {dim_scores} | **{overall}** | {demo} | {timeline} |")
        lines.append("")
        lines.append(f"*{copy['reason_note']}*")
        lines.append("")

    return '\n'.join(lines)


def _render_email_drafts_markdown(
    all_drafts: list,
    report: SegmentReport,
    df: pd.DataFrame,
) -> str:
    """Render email drafts. all_drafts is list of (segment_id, lang, SingleEmailDraft, members_df)."""
    seg_names = {s.segment_id: s.name for s in report.segments}

    lines = [
        "# Email drafts (by segment × language)",
        "",
        "> These drafts can be pasted into HubSpot Sequence/Template.",
        "> `{{contact.firstname}}` and similar fields are HubSpot personalization tokens.",
        "> Language versions are generated from detected recipient language; each recipient gets one language version only.",
        "",
    ]

    # Group by segment
    current_seg = None
    for sid, lang, draft, lang_members in all_drafts:
        if sid != current_seg:
            seg_members = df[df['_segment'] == sid]
            seg_label = seg_names.get(sid, sid)
            lines.append(f"## Segment {sid}: {seg_label} ({len(seg_members)} leads)")
            lines.append("")
            current_seg = sid

        recipient_names = ', '.join(
            str(r.get('name', r.get('email', '')))
            for _, r in lang_members.iterrows()
        )
        lang_label = LANG_LABELS.get(lang, lang)
        lines.append(f"### {lang_label} ({len(lang_members)} leads: {recipient_names})")
        lines.append("")
        lines.append(f"**Subject:** {draft.subject}")
        lines.append("")
        lines.append(draft.body)
        lines.append("")

    return '\n'.join(lines)
