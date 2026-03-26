import re

TITLE_KEYWORDS = sorted([
    '副總經理', '總經理', '資訊長', '資安長', '資安總監',
    '副總',
    '副處長', '處長', '部長', '總監', '協理',
    '資深經理', '技術經理', '經理', '副理',
    '主任', '主管',
    'CIO', 'CTO', 'CFO', 'CEO', 'COO', 'CISO',
    'Technology Director', 'Data Architect', 'IT head',
    'Director', 'VP', 'Architect',
], key=len, reverse=True)

DEPT_KEYWORDS = sorted([
    '資訊部', '資訊處', '電金部', '資訊室',
    '行政中心', '數據營運處', '創研AI部門',
    '電子商務',
], key=len, reverse=True)

COMPANY_SUFFIXES = [
    '股份有限公司', '有限公司', '公司',
    '企業', '集團', '投控',
    '銀行', '投信', '證券', '人壽',
    '醫院',
    '科技', '電子',
    '石化', '建材', '能源',
]

PERSONAL_EMAIL_DOMAINS = {
    'gmail.com', 'yahoo.com', 'yahoo.com.tw', 'hotmail.com',
    'outlook.com', 'icloud.com', 'qq.com', '163.com', 'me.com',
    'msn.com', 'live.com', 'mail.com',
}

TLD_TYPO_MAP = {
    '.xom': '.com',
    '.con': '.com',
    '.cmo': '.com',
    '.ocm': '.com',
    '.coom': '.com',
}


def parse_company_title(raw):
    """Parse combined company+title field.

    Returns (company, title, confidence) where confidence is
    'high', 'medium', or 'low'.
    """
    if not raw or str(raw).strip() in ('', 'nan', 'None'):
        return ('', '', 'low')

    raw = re.sub(r'\s+', ' ', str(raw).strip())

    if re.match(r'^[A-Za-z\s.,]+$', raw):
        for kw in TITLE_KEYWORDS:
            if kw.lower() in raw.lower():
                return ('', raw, 'medium')
        return (raw, '', 'medium')

    for kw in TITLE_KEYWORDS:
        if raw == kw:
            return ('', raw, 'medium')

    for sep in (' ', '/', '-'):
        if sep in raw:
            idx = raw.index(sep)
            left = raw[:idx].strip()
            right = raw[idx + 1:].strip()
            if not left:
                return ('', right, 'medium')
            if not right:
                return (left, '', 'medium')
            return (left, right, 'high')

    for kw in TITLE_KEYWORDS:
        idx = raw.rfind(kw)
        if idx > 0:
            company = raw[:idx]
            title = raw[idx:]
            for dept in DEPT_KEYWORDS:
                dept_idx = company.rfind(dept)
                if dept_idx >= 0 and dept_idx + len(dept) == len(company):
                    title = dept + title
                    company = company[:dept_idx]
                    break
            if company:
                return (company.strip(), title.strip(), 'medium')

    for suffix in COMPANY_SUFFIXES:
        if raw.endswith(suffix):
            return (raw, '', 'medium')

    if len(raw) <= 6:
        return (raw, '', 'medium')

    return (raw, '', 'low')


def clean_email(email):
    """Clean and validate email.

    Returns (cleaned_email, email_type, issues_string).
    """
    if not email or str(email).strip() in ('', 'nan', 'None'):
        return ('', 'missing', 'missing email')

    email = re.sub(r'\s+', '', str(email).strip()).lower()
    issues = []

    for typo, fix in TLD_TYPO_MAP.items():
        if email.endswith(typo):
            issues.append(f'TLD fixed: {typo} -> {fix}')
            email = email[:-len(typo)] + fix
            break

    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        issues.append('invalid format')

    domain = email.split('@')[-1] if '@' in email else ''
    email_type = 'personal' if domain in PERSONAL_EMAIL_DOMAINS else 'corporate'

    return (email, email_type, '; '.join(issues) if issues else '')
