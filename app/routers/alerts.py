"""
Real-time alerts: SSE when new loads match vehicle + empty postcode (+ optional base for corridor).
"""
import asyncio
import json
import queue
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.alert_stream import add_subscription, remove_subscription

router = APIRouter()

KEEPALIVE_SECONDS = 25


async def _event_generator(
    vehicle_id: int,
    origin_postcode: str,
    destination_postcode: Optional[str],
    request: Request,
):
    """Yield SSE events: keepalive comments and new_load data."""
    q = add_subscription(vehicle_id, origin_postcode, destination_postcode)
    try:
        loop = asyncio.get_event_loop()
        while True:
            try:
                msg = await loop.run_in_executor(
                    None,
                    lambda: q.get(timeout=KEEPALIVE_SECONDS),
                )
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
            if getattr(request, "is_disconnected", None) and request.is_disconnected():
                break
    finally:
        remove_subscription(q)


@router.get("/stream")
async def alerts_stream(
    request: Request,
    vehicle_id: int,
    origin_postcode: str,
    destination_postcode: Optional[str] = None,
) -> StreamingResponse:
    """
    Server-Sent Events: real-time when a new open load matches this vehicle.
    - With origin only: pickup within default radius (e.g. 25mi) of empty location.
    - With origin + destination (base): same as Find Backhaul — corridor from empty→base
      plus pickup near origin (merged).
    """
    origin_postcode = (origin_postcode or "").strip()
    if not origin_postcode:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="origin_postcode required")
    dest = (destination_postcode or "").strip() or None

    return StreamingResponse(
        _event_generator(vehicle_id, origin_postcode, dest, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
