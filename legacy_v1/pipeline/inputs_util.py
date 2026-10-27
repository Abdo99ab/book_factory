"""Raised Ready create-job helpers (Step 3)."""

from __future__ import annotations

import re
from typing import Any

from .catalogue import get_title

ORDER_ID_RE = re.compile(r"^[A-Za-z0-9-]+$")


def parse_age_band_range(age_band: str) -> tuple[float, float] | None:
    match = re.match(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", (age_band or "").strip())
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def age_band_warning(age_raw: str, title_age_band: str) -> str | None:
    try:
        age = float(str(age_raw).strip())
    except ValueError:
        return None
    band = parse_age_band_range(title_age_band)
    if not band:
        return None
    lo, hi = band
    if age < lo - 0.5 or age > hi + 1:
        return f"This title is written for ages {title_age_band}."
    return None


def collect_slots(form: Any, title_id: str) -> dict[str, str]:
    title = get_title(title_id)
    slots: dict[str, str] = {}
    for slot in title.get("personalisation_slots") or []:
        slot_id = slot.get("id")
        if not slot_id:
            continue
        value = (form.get(f"slot_{slot_id}") or "").strip()
        if value:
            slots[slot_id] = value
        elif slot.get("required"):
            raise ValueError(f"Missing required slot: {slot_id}")
    return slots


def validate_order_id(order_id: str) -> bool:
    return bool(order_id and ORDER_ID_RE.fullmatch(order_id.strip()))
