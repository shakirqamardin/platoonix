"""
Real-time alerts for hauliers: SSE stream when new loads match their vehicle + location.
"""
import asyncio
import json
import queue

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.alert_stream import add_subscription, remove_subscription

router = APIRouter()

KEEPALIVE_SECONDS = 25


async def _event_generator(vehicle_id: int, origin_postcode: str, request: Request):
    """Yield SSE events: keepalive comments and new_load data."""
    q = add_subscription(vehicle_id, origin_postcode)
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
) -> StreamingResponse:
    """
    Server-Sent Events stream: open this URL to get real-time alerts when a new load
    is posted that matches this vehicle and is within 25 miles of origin_postcode.
    Use the same matching rules as Find backhaul (trailer type, capacity).
    """
    origin_postcode = (origin_postcode or "").strip()
    if not origin_postcode:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="origin_postcode required")

    return StreamingResponse(
        _event_generator(vehicle_id, origin_postcode, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
