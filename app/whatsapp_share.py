"""Pre-filled WhatsApp share URLs for loads (api.whatsapp.com/send)."""
from __future__ import annotations

import re
from urllib.parse import quote

from app import models


def load_share_message(load: models.Load, base_url: str) -> str:
    """Plain-text message for sharing an open load (UTF-8; URL-encoded when sent)."""
    req = getattr(load, "requirements", None) or {}
    if not isinstance(req, dict):
        req = {}
    vt = (req.get("vehicle_type") or "any").strip() or "any"
    tt = (req.get("trailer_type") or "any").strip() or "any"
    vehicle = vt.replace("_", " ").title()
    trailer = tt.replace("_", " ").title()
    feats: list[str] = []
    if getattr(load, "requires_tail_lift", False):
        feats.append("Tail lift")
    if getattr(load, "requires_forklift", False):
        feats.append("Forklift")
    if getattr(load, "requires_temp_control", False):
        feats.append("Temp control")
    if getattr(load, "requires_adr", False):
        feats.append("ADR")
    features_str = ", ".join(feats) if feats else "None specified"
    if load.budget_gbp is not None:
        pay = f"£{float(load.budget_gbp):.2f}"
    else:
        pay = "TBC"
    root = (base_url or "https://web-production-7ca42.up.railway.app").rstrip("/")
    link = f"{root}/?section=find&load_id={load.id}"
    return (
        "🚛 Backhaul Available on Platoonix!\n\n"
        f"Route: {load.pickup_postcode} → {load.delivery_postcode}\n"
        f"Vehicle: {vehicle} • Trailer: {trailer}\n"
        f"Features needed: {features_str}\n"
        f"Payment: {pay}\n\n"
        f"View & book: {link}"
    )


def build_whatsapp_send_url(load: models.Load, base_url: str) -> str:
    """https://api.whatsapp.com/send?text=... (works on mobile and desktop browsers)."""
    msg = load_share_message(load, base_url)
    return "https://api.whatsapp.com/send?text=" + quote(msg, safe="")


def build_driver_support_whatsapp_url(
    job_summary: str,
    route_line: str,
    support_whatsapp_e164: str | None,
) -> str:
    """
    Driver help: breakdown, site closed, etc. Opens WhatsApp with prefilled context.
    If support_whatsapp_e164 is set (digits, country code), uses https://wa.me/<digits>?text=...
    Otherwise opens api.whatsapp.com/send?text=... (user picks contact).
    """
    body = (
        "Platoonix — driver help needed\n\n"
        f"{job_summary}\n"
        f"{route_line}\n\n"
        "Please describe what happened (e.g. breakdown, site closed, running late)."
    )
    q = quote(body, safe="")
    raw = (support_whatsapp_e164 or "").strip()
    digits = re.sub(r"\D", "", raw) if raw else ""
    if len(digits) >= 10:
        return f"https://wa.me/{digits}?text={q}"
    return "https://api.whatsapp.com/send?text=" + q
