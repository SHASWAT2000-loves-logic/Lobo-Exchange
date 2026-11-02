"""Groq image-to-listing suggestions. Nothing here publishes a listing."""

import base64
import json
import logging
import math
import os
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from groq import APIConnectionError, Groq
from pydantic import BaseModel, ConfigDict


load_dotenv(Path(__file__).resolve().parent / ".env")
logger = logging.getLogger(__name__)


class AISuggestionError(Exception):
    """The assistant could not produce safe, usable listing suggestions."""

    def __init__(self, message: str, reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


class ListingSuggestions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    category: str
    estimated_price_low: float
    estimated_price_high: float


class SafetySignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suspicious_language: bool
    spam_like: bool
    image_personal_info: bool


class SafetyReview(BaseModel):
    risk_level: Literal["low", "medium", "high"]
    warnings: list[str]
    reasons: list[str]
    ai_unavailable: bool


PERSONAL_PATTERNS = (
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "email address"),
    (re.compile(r"(?<!\w)(?:\+?1[\s().-]*)?(?:\(?\d{3}\)?[\s.-]*)\d{3}[\s.-]*\d{4}(?!\w)"), "phone number"),
    (re.compile(r"\b\d{1,6}\s+(?:[A-Za-z0-9.-]+\s+){1,5}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Place|Pl|Way)\.?\b", re.I), "street address"),
    (re.compile(r"\b(?:student|unm|banner)\s*(?:id|number)\s*[:#-]?\s*[A-Za-z0-9-]{4,}\b", re.I), "student ID"),
)
ADVANCE_PAYMENT_PATTERN = re.compile(
    r"\b(?:pay(?:ment)?|deposit)\b.{0,80}\b(?:immediately|before pickup|upfront|in advance)\b"
    r"|\b(?:immediately|before pickup|upfront|in advance)\b.{0,80}\b(?:pay(?:ment)?|deposit)\b",
    re.I | re.S,
)
SELLER_ATTRIBUTION = re.compile(
    r"(^|[.!?]\s+)(?:(?:the\s+)?seller\s+(?:notes|says|states)(?:\s+that)?|according\s+to\s+the\s+seller,?)\s+([a-z])",
    re.I,
)


def generate_listing_suggestions(
    image_bytes: bytes, mime_type: str, categories: tuple[str, ...],
    seller_input: dict[str, str] | None = None,
) -> dict:
    """Return validated, editable suggestions from an item photo and seller draft."""
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "").strip() or "qwen/qwen3.8-27b"
    if not api_key:
        raise AISuggestionError("Groq API key is missing", reason="missing_key")
    if not image_bytes or not mime_type.startswith("image/"):
        raise AISuggestionError("Image is missing or invalid")

    schema = ListingSuggestions.model_json_schema()
    schema["properties"]["category"]["enum"] = list(categories)
    draft = {key: (seller_input or {}).get(key, "") for key in ("title", "description", "category", "price")}
    prompt = (
        "Suggest a concise marketplace listing using both the item photo and the seller's existing draft. "
        "Treat explicit seller-provided item facts as primary, including condition, usage duration, and included items. "
        "Preserve useful seller facts while improving wording. Use the photo to add visible details and fill gaps. "
        "Do not claim that seller-provided facts were visually verified. Do not silently contradict the seller. "
        "Write the description as natural listing copy, not a report about the seller. "
        "Avoid phrases such as 'the seller notes', 'the seller says', or 'according to the seller'. "
        "State seller-provided facts directly without implying they were confirmed from the image. "
        "Do not invent facts that are neither visible nor provided by the seller. "
        "Choose exactly one category from: " + ", ".join(categories) + ". "
        "Estimate a broad, approximate used-item price range in US dollars, considering but not simply copying the seller's asking price. "
        "The range is a suggestion, not a guaranteed market value. "
        "Keep the title under 120 characters and the description under 5,000 characters. "
        "Seller draft (user-provided, not visually verified): " + json.dumps(draft, ensure_ascii=False)
    )
    try:
        image_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        with Groq(api_key=api_key, timeout=20.0, max_retries=0) as client:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]}],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "listing_suggestions", "strict": True, "schema": schema,
                }},
                max_completion_tokens=512,
            )
        suggestions = ListingSuggestions.model_validate_json(response.choices[0].message.content)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        logger.warning(
            "Groq listing suggestions failed (%s, HTTP %s)",
            type(exc).__name__, status if isinstance(status, int) else "unknown",
        )
        if isinstance(exc, APIConnectionError):
            reason = "connection"
        elif status in (401, 403):
            reason = "auth"
        elif status == 503:
            reason = "busy"
        elif status == 429:
            reason = "rate_limit"
        else:
            reason = "unavailable"
        raise AISuggestionError("Groq request or response failed", reason=reason) from exc

    title = suggestions.title.strip()
    description = SELLER_ATTRIBUTION.sub(
        lambda match: match.group(1) + match.group(2).upper(),
        suggestions.description.strip(),
    )
    category = suggestions.category.strip()
    low = suggestions.estimated_price_low
    high = suggestions.estimated_price_high
    if (
        not 1 <= len(title) <= 120
        or not 1 <= len(description) <= 5000
        or category not in categories
        or not math.isfinite(low)
        or not math.isfinite(high)
        or not 0 <= low <= high <= 99999999.99
    ):
        raise AISuggestionError("Groq returned invalid listing values")

    cents = Decimal("0.01")
    return {
        "title": title,
        "description": description,
        "category": category,
        "estimated_price_low": str(Decimal(str(low)).quantize(cents, rounding=ROUND_HALF_UP)),
        "estimated_price_high": str(Decimal(str(high)).quantize(cents, rounding=ROUND_HALF_UP)),
    }


def analyze_listing_safety(
    title: str, description: str, category: str, price: Decimal,
    image_bytes: bytes, mime_type: str,
    estimated_price_low: str = "", estimated_price_high: str = "",
) -> dict:
    """Return advisory warnings. A failed AI call leaves local checks available."""
    warnings = []
    reasons = []
    listing_text = f"{title}\n{description}"
    redacted_text = listing_text
    for pattern, label in PERSONAL_PATTERNS:
        if pattern.search(listing_text):
            warnings.append(f"Your listing may include a {label}.")
            reasons.append(f"Consider removing the {label} before publishing.")
        redacted_text = pattern.sub(f"[{label} removed]", redacted_text)

    payment_pressure = bool(ADVANCE_PAYMENT_PATTERN.search(listing_text))
    if payment_pressure:
        warnings.append("Some wording may warrant extra caution.")
        reasons.append("The listing asks for payment under time pressure or before pickup. Review the wording before publishing.")

    try:
        low = Decimal(estimated_price_low)
        high = Decimal(estimated_price_high)
        has_guidance = low.is_finite() and high.is_finite() and 0 <= low <= high <= Decimal("99999999.99")
    except (InvalidOperation, TypeError):
        has_guidance = False
    if has_guidance and (price < low * Decimal("0.5") or price > high * Decimal("1.5")):
        warnings.append("Your asking price is far outside the earlier AI estimate.")
        reasons.append("The estimate is approximate; review the price before posting.")

    ai_unavailable = False
    try:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise AISuggestionError("Groq API key is missing")
        if not image_bytes or not mime_type.startswith("image/"):
            raise AISuggestionError("Image is missing or invalid")
        prompt = (
            "Review this UNM student marketplace listing and photo for advisory signals only. "
            "Set suspicious_language true only for scam-like wording such as unusual advance-payment requests, "
            "pressure, or implausible claims. Set spam_like true for repetition or unrelated promotion. "
            "Set image_personal_info true if the photo visibly shows a phone number, personal email, "
            "street address, student ID, or similar identifying information. Be conservative. "
            "Do not assert that the seller is fraudulent or repeat personal details. "
            f"Listing category: {category}. Asking price: ${price}. "
            f"Listing text (contact details removed): {redacted_text}"
        )
        image_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        with Groq(
            api_key=api_key, timeout=20.0, max_retries=0,
        ) as client:
            response = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "").strip() or "qwen/qwen3.8-27b",
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]}],
                response_format={"type": "json_schema", "json_schema": {
                    "name": "listing_safety_signals", "strict": True,
                    "schema": SafetySignals.model_json_schema(),
                }},
                max_completion_tokens=256,
            )
        signals = SafetySignals.model_validate_json(response.choices[0].message.content)
        if signals.suspicious_language and not payment_pressure:
            warnings.append("Some wording may warrant extra caution.")
            reasons.append("The AI review noticed wording that resembles common scam patterns. This is not a finding of fraud.")
        if signals.spam_like:
            warnings.append("Some text may read like spam or promotion.")
            reasons.append("Consider removing repetition or unrelated promotional language.")
        if signals.image_personal_info:
            warnings.append("The photo may show personal identifying information.")
            reasons.append("Review the photo for contact details, addresses, or ID numbers before publishing.")
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        logger.warning(
            "Groq safety review unavailable (%s, HTTP %s)",
            type(exc).__name__, status if isinstance(status, int) else "unknown",
        )
        ai_unavailable = True

    review = SafetyReview(
        risk_level="high" if len(warnings) >= 3 else "medium" if warnings else "low",
        warnings=warnings,
        reasons=reasons,
        ai_unavailable=ai_unavailable,
    )
    return review.model_dump()
