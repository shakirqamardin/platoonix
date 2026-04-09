"""Transactional emails for cancellation / no-show flows."""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app import models
from app.services.email_sender import send_email
from app.services.in_app_notifications import (
    haulier_office_user_ids,
    loader_office_user_ids,
    record_user_notifications,
)

logger = logging.getLogger(__name__)


def _haulier_name(h: models.Haulier) -> str:
    return (h.name or "").strip() or "Haulier"


def notify_hauliers_loader_cancelled(
    db: Session,
    job: models.BackhaulJob,
    load: models.Load,
    fee_gbp: float,
    tier_key: str,
    *,
    commit_in_app: bool = True,
) -> None:
    """Loader cancelled — no fee language; fee_gbp/tier_key kept for call-site compatibility."""
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    if not vehicle:
        return
    haulier = db.get(models.Haulier, vehicle.haulier_id)
    users = db.query(models.User).filter(models.User.haulier_id == vehicle.haulier_id).all()
    jref = job.display_number
    route = f"{load.pickup_postcode} → {load.delivery_postcode}"
    pickup_s = ""
    if load.pickup_window_start:
        pickup_s = load.pickup_window_start.strftime("%d %b %Y, %H:%M")
    subj = "Load cancelled by loader"
    body = (
        f"The loader has cancelled the following job:\n\n"
        f"Job: {jref}\n"
        f"Route: {route}\n"
        f"Pickup: {pickup_s or 'Not specified'}\n\n"
        "You are free to accept other loads on Platoonix.\n\n"
        "We apologise for the inconvenience.\n\n"
        "Best regards,\nPlatoonix Team\nsupport@platoonix.co.uk\n"
    )
    priority = "normal"
    if haulier and haulier.contact_email:
        send_email(haulier.contact_email, subj, body)
    for u in users:
        if u.email and u.email != (haulier.contact_email if haulier else None):
            send_email(u.email, subj, body)

    try:
        uids = haulier_office_user_ids(db, vehicle.haulier_id)
        record_user_notifications(
            db,
            uids,
            title=subj[:255],
            body=body,
            link_url="/?section=matches",
            kind="loader_cancel",
            priority=priority,
            commit=commit_in_app,
        )
    except Exception:
        logger.exception("in-app notify_hauliers_loader_cancelled")


def notify_loader_haulier_cancelled(
    db: Session,
    job: models.BackhaulJob,
    hours_until: float,
    *,
    commit_in_app: bool = True,
) -> None:
    load = db.get(models.Load, job.load_id)
    if not load or not load.loader_id:
        return
    loader = db.get(models.Loader, load.loader_id)
    vehicle = db.get(models.Vehicle, job.vehicle_id)
    haulier = db.get(models.Haulier, vehicle.haulier_id) if vehicle else None
    if not loader or not loader.contact_email:
        return
    hn = _haulier_name(haulier) if haulier else "The haulier"
    jref = job.display_number
    pickup_s = ""
    if load.pickup_window_start:
        pickup_s = load.pickup_window_start.strftime("%d %b %Y, %H:%M")
    subj = "Haulier cancelled your job"
    body = (
        f"Unfortunately, {hn} has cancelled your job:\n\n"
        f"Job: {jref}\n"
        f"Route: {load.pickup_postcode} → {load.delivery_postcode}\n"
        f"Pickup: {pickup_s or 'Not specified'}\n\n"
        "What we've done:\n"
        "• Your load has been reopened automatically\n"
        "• You will receive a full refund where a payment was taken\n"
        "• Other hauliers can now see and accept your load\n\n"
        "You don't need to do anything — the load is live again.\n\n"
        "Need help? Email support@platoonix.co.uk\n\n"
        "Best regards,\nPlatoonix Team\n"
    )
    send_email(loader.contact_email, subj, body)

    priority = "important"
    try:
        uids = loader_office_user_ids(db, load.loader_id)
        record_user_notifications(
            db,
            uids,
            title=subj[:255],
            body=body,
            link_url="/?section=loads",
            kind="haulier_cancel",
            priority=priority,
            commit=commit_in_app,
        )
    except Exception:
        logger.exception("in-app notify_loader_haulier_cancelled")


def notify_loader_emergency_haulier_cancel(
    db: Session,
    job: models.BackhaulJob,
    reason: str,
    hours_until: float,
    *,
    commit_in_app: bool = True,
) -> None:
    load = db.get(models.Load, job.load_id)
    if not load or not load.loader_id:
        return
    loader = db.get(models.Loader, load.loader_id)
    if not loader or not loader.contact_email:
        return
    subj = "Emergency haulier cancellation"
    body = (
        f"Your haulier reported an emergency cancellation ({reason}) for "
        f"{load.pickup_postcode} → {load.delivery_postcode}. "
        f"Time until pickup was about {int(max(0, hours_until))} hours.\n\n"
        "The load may be reopened as priority. Our team may follow up. support@platoonix.co.uk\n"
    )
    send_email(loader.contact_email, subj, body)

    priority = "critical" if hours_until < 12.0 else "important"
    try:
        uids = loader_office_user_ids(db, load.loader_id)
        record_user_notifications(
            db,
            uids,
            title=subj[:255],
            body=body,
            link_url="/?section=loads",
            kind="emergency_cancellation",
            priority=priority,
            commit=commit_in_app,
        )
    except Exception:
        logger.exception("in-app notify_loader_emergency_haulier_cancel")


def send_haulier_emergency_evidence_reminder(
    db: Session,
    haulier: models.Haulier,
    job: models.BackhaulJob,
    reason: str,
    *,
    commit_in_app: bool = True,
) -> None:
    if not haulier.contact_email:
        return
    jref = job.display_number
    subj = f"Action required: emergency evidence — job {jref}"
    body = (
        f"You cancelled job {jref} as an emergency ({reason}).\n\n"
        "Please email evidence to support@platoonix.co.uk within 24 hours (breakdown invoice, police ref, etc.).\n"
    )
    send_email(haulier.contact_email, subj, body)

    try:
        uids = haulier_office_user_ids(db, haulier.id)
        record_user_notifications(
            db,
            uids,
            title=subj[:255],
            body=body,
            link_url="/?section=matches",
            kind="emergency_evidence",
            priority="critical",
            commit=commit_in_app,
        )
    except Exception:
        logger.exception("in-app send_haulier_emergency_evidence_reminder")


def send_haulier_probation_notice(haulier: models.Haulier) -> None:
    if not haulier.contact_email:
        return
    send_email(
        haulier.contact_email,
        "Platoonix: account on probation (cancellation strikes)",
        "Your account is on probation due to repeated late cancellations. "
        "Further strikes may lead to suspension. support@platoonix.co.uk\n",
    )


def send_haulier_suspension_notice(haulier: models.Haulier) -> None:
    if not haulier.contact_email:
        return
    send_email(
        haulier.contact_email,
        "Platoonix: account suspended (cancellation strikes)",
        "Your account has been suspended due to cancellation policy breaches. "
        "Contact support@platoonix.co.uk to discuss reactivation.\n",
    )


def notify_support_emergency_cancellation(
    job: models.BackhaulJob,
    reason: str,
    details: str,
) -> None:
    jref = job.display_number
    subj = f"Emergency haulier cancellation — {jref}"
    body = f"Job {jref} (id {job.id})\nReason: {reason}\nDetails: {details}\n"
    send_email("support@platoonix.co.uk", subj, body)


def notify_no_show_report(
    db: Session,
    job: models.BackhaulJob,
    load: models.Load,
    haulier: models.Haulier,
    loader: models.Loader,
    *,
    commit_in_app: bool = True,
) -> None:
    jref = job.display_number
    route = f"{load.pickup_postcode}→{load.delivery_postcode}"

    if loader.contact_email:
        send_email(
            loader.contact_email,
            f"No-show report recorded — job {jref}",
            "We recorded your report. Our team may contact you. support@platoonix.co.uk\n",
        )
    if haulier.contact_email:
        send_email(
            haulier.contact_email,
            f"URGENT: No-show report — job {jref}",
            "A loader reported a no-show or serious issue. Respond to support@platoonix.co.uk urgently with context.\n",
        )
    send_email(
        "support@platoonix.co.uk",
        f"No-show report job {jref}",
        f"Load {route} haulier {haulier.id} loader {loader.id}\n",
    )

    try:
        loader_uids = loader_office_user_ids(db, loader.id)
        record_user_notifications(
            db,
            loader_uids,
            title=f"No-show report recorded — {jref}",
            body=f"Your report for {route} was logged. Refunds follow policy.",
            link_url="/?section=matches",
            kind="no_show_loader",
            priority="important",
            commit=commit_in_app,
        )
        haulier_uids = haulier_office_user_ids(db, haulier.id)
        record_user_notifications(
            db,
            haulier_uids,
            title=f"No-show report — {jref}",
            body=f"A loader reported a no-show on {route}. Respond via support if needed.",
            link_url="/?section=matches",
            kind="no_show_haulier",
            priority="critical",
            commit=commit_in_app,
        )
    except Exception:
        logger.exception("in-app notify_no_show_report")


def notify_support_evidence_submitted(job_id: int, haulier_id: int, path: str, notes: Optional[str]) -> None:
    send_email(
        "support@platoonix.co.uk",
        f"Emergency evidence submitted — job {job_id}",
        f"Haulier {haulier_id}\nFile: {path}\nNotes: {notes or ''}\n",
    )
