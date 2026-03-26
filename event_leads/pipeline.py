import os
import pickle
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml
from rapidfuzz import fuzz

from .parsers import parse_company_title, clean_email


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _cp_dir(output_dir: Path) -> Path:
    d = output_dir / 'checkpoints'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_cp(df: pd.DataFrame, output_dir: Path, stage: str) -> None:
    path = _cp_dir(output_dir) / f'{stage}.pkl'
    with open(path, 'wb') as f:
        pickle.dump(df, f)
    print(f'  [checkpoint] saved: {stage} ({len(df)} rows → {path.name})')


def _load_cp(output_dir: Path, stage: str) -> Optional[pd.DataFrame]:
    path = _cp_dir(output_dir) / f'{stage}.pkl'
    if path.exists():
        with open(path, 'rb') as f:
            df = pickle.load(f)
        print(f'  [checkpoint] loaded: {stage} — skipping this stage')
        return df
    return None


def load_config(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Stage 1: Ingest
# ---------------------------------------------------------------------------

def ingest_source(source_name, source_config, data_dir):
    """Read one data source and apply column mapping."""
    file_path = data_dir / source_config['file']
    encoding = source_config.get('encoding', 'utf-8')
    file_type = source_config.get('type', 'csv')

    if file_type == 'csv':
        df = pd.read_csv(file_path, encoding=encoding, dtype=str)
    elif file_type in ('xlsx', 'excel'):
        df = pd.read_excel(file_path, dtype=str)
    else:
        raise ValueError(f'Unsupported file type: {file_type}')

    df = df.fillna('')

    result = pd.DataFrame()

    mapping = source_config.get('mapping', {})
    for std_col, src_col in mapping.items():
        if src_col in df.columns:
            result[std_col] = df[src_col].str.strip()
        else:
            result[std_col] = ''

    survey_fields = source_config.get('survey_fields', {})
    for std_col, src_col in survey_fields.items():
        if src_col in df.columns:
            result[std_col] = df[src_col].str.strip()
        else:
            result[std_col] = ''

    result['_source'] = source_name
    result['_attendance_status'] = source_config.get('attendance_status', '')

    return result


def ingest_all(config, config_dir):
    """Read all sources defined in config, return combined DataFrame."""
    data_dir = Path(config_dir) / config.get('data_dir', '.')
    data_dir = data_dir.resolve()

    frames = []
    for name, src_cfg in config.get('sources', {}).items():
        df = ingest_source(name, src_cfg, data_dir)
        frames.append(df)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Stage 2: Normalize
# ---------------------------------------------------------------------------

def normalize(df):
    """Parse company+title, clean emails, fill derived columns."""
    if 'company_title' in df.columns:
        parsed = df['company_title'].apply(parse_company_title)
        df['company'] = parsed.apply(lambda x: x[0])
        df['title'] = parsed.apply(lambda x: x[1])
        df['_parse_confidence'] = parsed.apply(lambda x: x[2])
        df['_company_title_raw'] = df['company_title']
        df = df.drop(columns=['company_title'])
    else:
        for col in ('company', 'title'):
            if col not in df.columns:
                df[col] = ''
        df['_parse_confidence'] = 'high'
        df['_company_title_raw'] = ''

    if 'email' in df.columns:
        cleaned = df['email'].apply(clean_email)
        df['email'] = cleaned.apply(lambda x: x[0])
        df['_email_type'] = cleaned.apply(lambda x: x[1])
        df['_email_issues'] = cleaned.apply(lambda x: x[2])
    else:
        df['email'] = ''
        df['_email_type'] = 'missing'
        df['_email_issues'] = 'missing email'

    return df


# ---------------------------------------------------------------------------
# Stage 2b: Deduplicate
# ---------------------------------------------------------------------------

def _pick_longer(a, b):
    """Return the longer non-empty string."""
    a = str(a).strip() if pd.notna(a) else ''
    b = str(b).strip() if pd.notna(b) else ''
    if not a:
        return b
    if not b:
        return a
    return a if len(a) >= len(b) else b


def _combine_values(a, b):
    """Combine two survey-type values; deduplicate if identical."""
    a = str(a).strip() if pd.notna(a) else ''
    b = str(b).strip() if pd.notna(b) else ''
    if not a:
        return b
    if not b:
        return a
    if a == b:
        return a
    return f'{a} | {b}'


def merge_duplicate_rows(group):
    """Merge multiple rows for the same lead into one."""
    if len(group) == 1:
        row = group.iloc[0].copy()
        row['_dedup_note'] = ''
        return row

    group = group.sort_values('timestamp', ascending=False) if 'timestamp' in group.columns else group

    merged = group.iloc[0].copy()

    identity_cols = ['name', 'company', 'title', 'email', 'phone']
    for col in identity_cols:
        if col in group.columns:
            merged[col] = _pick_longer(group.iloc[0][col], group.iloc[1][col])

    survey_cols = ['interest_scenario', 'demo_interest', 'project_timeline', 'additional_topics']
    for col in survey_cols:
        if col in group.columns:
            vals = group[col].tolist()
            combined = vals[0]
            for v in vals[1:]:
                combined = _combine_values(combined, v)
            merged[col] = combined

    sources = group['_source'].unique().tolist()
    merged['_source'] = ', '.join(sources)

    merged['_dedup_note'] = f'merged {len(group)} records (email match)'
    return merged


def deduplicate(df, fuzzy_threshold=85):
    """Deduplicate by email exact match, then flag fuzzy candidates."""
    if df.empty:
        return df

    email_col = df['email'].str.lower().str.strip()
    results = []

    has_email = email_col != ''
    with_email = df[has_email].copy()
    without_email = df[~has_email].copy()

    for _, group in with_email.groupby(email_col[has_email]):
        merged = merge_duplicate_rows(group)
        results.append(merged)

    for _, row in without_email.iterrows():
        row = row.copy()
        row['_dedup_note'] = 'no email — review manually'
        results.append(row)

    if not results:
        return df.iloc[0:0]

    out = pd.DataFrame(results).reset_index(drop=True)

    if fuzzy_threshold and len(out) > 1:
        _flag_fuzzy_candidates(out, fuzzy_threshold)

    return out


def _flag_fuzzy_candidates(df, threshold):
    """Add _fuzzy_flag for rows that might be duplicates by name+company."""
    flags = [''] * len(df)
    names = df['name'].fillna('').tolist()
    companies = df['company'].fillna('').tolist()

    for i in range(len(df)):
        for j in range(i + 1, len(df)):
            if not names[i] or not names[j]:
                continue
            name_score = fuzz.token_sort_ratio(names[i], names[j])
            comp_score = fuzz.token_sort_ratio(companies[i], companies[j]) if companies[i] and companies[j] else 0
            combined = (name_score * 0.6 + comp_score * 0.4) if companies[i] and companies[j] else name_score
            if combined >= threshold:
                note = f'possible dup with row {j + 1} (score {combined:.0f})'
                flags[i] = note if not flags[i] else f'{flags[i]}; {note}'
                note_j = f'possible dup with row {i + 1} (score {combined:.0f})'
                flags[j] = note_j if not flags[j] else f'{flags[j]}; {note_j}'

    df['_fuzzy_flag'] = flags


# ---------------------------------------------------------------------------
# Stage 4: Output
# ---------------------------------------------------------------------------

OUTPUT_COLUMNS = [
    'name', 'company', 'title', 'email', 'phone',
    'interest_scenario', 'demo_interest', 'project_timeline', 'additional_topics',
    '_source', '_attendance_status', '_email_type', '_email_issues',
    '_company_title_raw', '_parse_confidence', '_dedup_note', '_fuzzy_flag',
]


def output_csv(df, output_path, config):
    """Write the clean CSV."""
    cols = [c for c in OUTPUT_COLUMNS if c in df.columns]
    extra = [c for c in df.columns if c not in OUTPUT_COLUMNS and c != 'timestamp' and c != 'company_title']
    out_df = df[cols + extra]

    event = config.get('event', {})
    out_df = out_df.copy()
    out_df['_event_name'] = event.get('name', '')
    out_df['_event_date'] = event.get('date', '')

    out_df.to_csv(output_path, index=False, encoding='utf-8-sig')
    return out_df


def print_stats(raw_df, clean_df, config):
    """Print processing summary."""
    event = config.get('event', {})
    name = event.get('name', 'Unknown Event')
    date = event.get('date', '')

    raw_count = len(raw_df)
    clean_count = len(clean_df)
    dupes = raw_count - clean_count

    print(f'\n{"=" * 50}')
    print(f'  Event Lead CLI v0')
    print(f'  {name} ({date})')
    print(f'{"=" * 50}\n')

    sources = raw_df['_source'].value_counts()
    print('Sources:')
    for src, cnt in sources.items():
        print(f'  {src}: {cnt} records')
    print()

    if '_parse_confidence' in clean_df.columns:
        conf = clean_df['_parse_confidence'].value_counts()
        print('Company/title parsing:')
        for level in ('high', 'medium', 'low'):
            print(f'  {level}: {conf.get(level, 0)}')
        print()

    if '_email_type' in clean_df.columns:
        types = clean_df['_email_type'].value_counts()
        print('Email types:')
        for t, c in types.items():
            print(f'  {t}: {c}')
        print()

    if '_email_issues' in clean_df.columns:
        issues = clean_df[clean_df['_email_issues'] != '']
        if len(issues):
            print(f'Email issues ({len(issues)}):')
            for _, row in issues.iterrows():
                print(f'  {row.get("name", "?")} — {row["_email_issues"]}')
            print()

    print(f'Dedup: {raw_count} raw → {clean_count} clean ({dupes} merged)')

    if '_dedup_note' in clean_df.columns:
        merged = clean_df[clean_df['_dedup_note'].str.contains('merged', na=False)]
        for _, row in merged.iterrows():
            print(f'  → {row.get("name", "?")} / {row.get("company", "?")} ({row["email"]})')

    if '_fuzzy_flag' in clean_df.columns:
        fuzzy = clean_df[clean_df['_fuzzy_flag'] != '']
        if len(fuzzy):
            print(f'\nFuzzy match candidates ({len(fuzzy)}):')
            for _, row in fuzzy.iterrows():
                print(f'  {row.get("name", "")} / {row.get("company", "")} — {row["_fuzzy_flag"]}')

    print(f'\nOutput: {clean_count} leads')
    print()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(config_path, output_dir=None, enrich=False, resume=False):
    """Execute the pipeline.

    resume=True: load completed stages from checkpoints, skip re-running them.
    Useful when a later stage (e.g. email) crashed and you don't want to
    re-pay for all the enrichment LLM calls.
    """
    config_path = Path(config_path).resolve()
    config_dir = config_path.parent
    config = load_config(config_path)

    if output_dir is None:
        output_dir = config_dir / 'output'
    else:
        output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prefix = config.get('output', {}).get('filename_prefix', 'leads')

    # ------------------------------------------------------------------
    # Stage 1: Ingest
    # ------------------------------------------------------------------
    raw_df = _load_cp(output_dir, 'ingest') if resume else None
    if raw_df is None:
        raw_df = ingest_all(config, config_dir)
        print(f'Loaded {len(raw_df)} raw records')
        _save_cp(raw_df, output_dir, 'ingest')
    else:
        print(f'Loaded {len(raw_df)} raw records (from checkpoint)')

    # ------------------------------------------------------------------
    # Stage 2: Normalize + Dedup
    # ------------------------------------------------------------------
    clean_df = _load_cp(output_dir, 'dedup') if resume else None
    if clean_df is None:
        clean_df = normalize(raw_df)
        clean_df = deduplicate(clean_df, config.get('dedup', {}).get('fuzzy_threshold', 85))
        _save_cp(clean_df, output_dir, 'dedup')

    # ------------------------------------------------------------------
    # Stage 3–5: LLM enrichment (most expensive — checkpoint after each)
    # ------------------------------------------------------------------
    if enrich:
        from .enrich import enrich_leads, generate_segment_report, generate_email_drafts

        # Stage 3: Enrich (per-lead scoring)
        enriched_df = _load_cp(output_dir, 'enrich') if resume else None
        if enriched_df is None:
            print('\nRunning LLM enrichment (with scoring)...')
            clean_df = enrich_leads(clean_df, config=config)
            _save_cp(clean_df, output_dir, 'enrich')
        else:
            clean_df = enriched_df

        # Stage 4: Segment report (cheap — always re-run from enriched data)
        print('\nGenerating segment report...')
        clean_df, report_md, segment_report = generate_segment_report(clean_df, config=config)

        report_path = output_dir / f'{prefix}-report.md'
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_md)
        print(f'Report saved to: {report_path}')

        # Stage 5: Email drafts (cheap — always re-run)
        print('\nGenerating email drafts...')
        email_md = generate_email_drafts(segment_report, clean_df, config)

        email_path = output_dir / f'{prefix}-email-drafts.md'
        with open(email_path, 'w', encoding='utf-8') as f:
            f.write(email_md)
        print(f'Email drafts saved to: {email_path}')

    output_path = output_dir / f'{prefix}-leads.csv'
    output_csv(clean_df, output_path, config)
    print_stats(raw_df, clean_df, config)
    print(f'Saved to: {output_path}')

    return clean_df
