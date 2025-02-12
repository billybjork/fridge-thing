from fastapi import APIRouter, Request, HTTPException
from app.schemas import DeviceDisplayRequest, DeviceDisplayResponse
from app.crud import get_or_create_device, log_event
import os

router = APIRouter()

@router.post("/api/display", response_model=DeviceDisplayResponse)
async def get_display(request_data: DeviceDisplayRequest, request: Request):
    """
    For now:
      1. Lookup (or create) the device in DB.
      2. Return the device's latest image & next wake time.
    """
    pool = request.app.state.pool  # we assume main.py sets up a pool
    async with pool.acquire() as conn:
        device_row = await get_or_create_device(conn, request_data.device_uuid)
        device_id = device_row["id"]

        # Log an event
        await log_event(conn, device_id, "wake", f"current_fw_ver={request_data.current_fw_ver}")

        # We’re ignoring firmware updates for now, so skip that logic.
        image_url = device_row.get("latest_image_url") or "https://fallback-s3-url.bmp"
        next_wake_secs = device_row.get("next_wake_secs", 3600)

        return DeviceDisplayResponse(
            image_url=image_url,
            next_wake_secs=next_wake_secs
        )