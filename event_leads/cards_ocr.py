"""Business card OCR helper: image folder -> CSV for pipeline sources."""

import base64
import mimetypes
import os
from pathlib import Path
from typing import List

import instructor
import pandas as pd
from openai import OpenAI
from pydantic import BaseModel, Field

from .parsers import clean_email

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class CardOCRResult(BaseModel):
    name: str = Field(default="", description="Full name if present")
    email: str = Field(default="", description="Primary email address")
    company: str = Field(default="", description="Company name")
    title: str = Field(default="", description="Job title")
    phone: str = Field(default="", description="Phone number")
    raw_text: str = Field(default="", description="Visible text summary from card")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="OCR confidence estimate")


def _openai_client_kwargs() -> dict:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing. Please export a valid API key.")
    if any(ord(ch) > 127 for ch in api_key):
        raise ValueError("OPENAI_API_KEY contains non-ASCII characters. Re-copy it as plain text.")
    if base_url and any(ord(ch) > 127 for ch in base_url):
        raise ValueError("OPENAI_BASE_URL contains non-ASCII characters. Re-copy it as plain text.")
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return kwargs


def _to_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _ocr_card(client, image_path: Path, model: str) -> CardOCRResult:
    data_url = _to_data_url(image_path)
    return client.chat.completions.create(
        model=model,
        response_model=CardOCRResult,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract contact information from a business card image. "
                    "Return empty string for unknown fields. "
                    "Do not guess information that is not visible."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read this business card and extract contact fields."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        max_retries=2,
    )


def run_cards_ocr(input_dir: str, output_csv: str, model: str = "gpt-4o-mini") -> pd.DataFrame:
    """Run OCR over all card images in input_dir and write output_csv."""
    src = Path(input_dir).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Input directory not found: {src}")

    images: List[Path] = sorted(p for p in src.iterdir() if p.suffix.lower() in SUPPORTED_EXTS and p.is_file())
    if not images:
        raise ValueError(f"No card images found in: {src} (supported: {', '.join(sorted(SUPPORTED_EXTS))})")

    out = Path(output_csv).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    client = instructor.from_openai(OpenAI(**_openai_client_kwargs()))
    rows = []
    total = len(images)
    print(f"[cards-ocr] {total} image(s) found in {src}")

    for i, img in enumerate(images, 1):
        print(f"[cards-ocr] {i}/{total}: {img.name}")
        try:
            res = _ocr_card(client, img, model)
            email, email_type, email_issues = clean_email(res.email)
            company = (res.company or "").strip()
            title = (res.title or "").strip()
            company_title = " ".join([x for x in [company, title] if x]).strip()
            rows.append(
                {
                    "name": (res.name or "").strip(),
                    "email": email,
                    "company": company,
                    "title": title,
                    "company_title": company_title,
                    "phone": (res.phone or "").strip(),
                    "raw_text": (res.raw_text or "").strip(),
                    "_card_ocr_confidence": round(float(res.confidence), 3),
                    "_card_image_file": img.name,
                    "_email_type": email_type,
                    "_email_issues": email_issues,
                }
            )
        except Exception as e:
            rows.append(
                {
                    "name": "",
                    "email": "",
                    "company": "",
                    "title": "",
                    "company_title": "",
                    "phone": "",
                    "raw_text": "",
                    "_card_ocr_confidence": 0.0,
                    "_card_image_file": img.name,
                    "_email_type": "missing",
                    "_email_issues": f"ocr_error: {e}",
                }
            )

    df = pd.DataFrame(rows)
    if "email" in df.columns:
        dup_mask = df["email"].fillna("").str.lower().duplicated(keep=False) & (df["email"].fillna("") != "")
        df["_dup_email_flag"] = dup_mask.map(lambda x: "possible_duplicate" if x else "")
    else:
        df["_dup_email_flag"] = ""

    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[cards-ocr] CSV saved: {out}")
    return df
