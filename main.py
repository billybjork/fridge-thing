import os
import random
from contextlib import asynccontextmanager
from typing import Optional, Callable, Awaitable

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, Request
from pydantic import BaseModel

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

load_dotenv()

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL")

# S3 configuration (remove?)
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_REGION = os.getenv("S3_REGION")

# Server configuration
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))

# Default fallback image (used if no valid image is found)
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/test.bmp"

# ------------------------------------------------------------------------------
# Database Operations
# ------------------------------------------------------------------------------

async def get_or_create_device(conn: asyncpg.Connection, device_uuid: str) -> dict:
    """
    Retrieve or create a device entry. Returns device data including channel_id.
    """
    row = await conn.fetchrow(
        "SELECT * FROM devices WHERE device_uuid = $1", device_uuid
    )
    if not row:
        device_id = await conn.fetchval(
            "INSERT INTO devices (device_uuid) VALUES ($1) RETURNING id", device_uuid
        )
        return {
            "id": device_id,
            "device_uuid": device_uuid,
            "channel_id": None,
            "next_wake_secs": 3600,
            "image_url": None
        }
    return dict(row)

async def log_event(conn: asyncpg.Connection, device_id: int, event_type: str, message: str = "") -> None:
    """
    Log an event (e.g., device wake-up).
    """
    await conn.execute(
        """
        INSERT INTO device_logs (device_id, event_type, message)
        VALUES ($1, $2, $3)
        """,
        device_id, event_type, message
    )

# ------------------------------------------------------------------------------
# Pydantic Schemas
# ------------------------------------------------------------------------------

class DeviceDisplayRequest(BaseModel):
    current_fw_ver: Optional[str] = None  # OTA updates ignored for now
    battery_voltage: Optional[float] = None
    wifi_signal: Optional[int] = None

class DeviceDisplayResponse(BaseModel):
    image_url: str
    next_wake_secs: int

# ------------------------------------------------------------------------------
# Image Selection Based on Channel
# ------------------------------------------------------------------------------

async def get_random_image_url(conn: asyncpg.Connection) -> str:
    """
    Select a random image from the assets table.
    Ensures a valid URL is always returned.
    """
    row = await conn.fetchrow(
        "SELECT image_proxy_s3_object_url FROM assets WHERE image_proxy_s3_object_url IS NOT NULL ORDER BY random() LIMIT 1"
    )
    return row["image_proxy_s3_object_url"] if row else DEFAULT_FALLBACK_IMAGE

async def get_daily_image_url(conn: asyncpg.Connection) -> str:
    """
    Select the 'daily' image from the assets table.
    Ensures a valid URL is always returned.
    """
    row = await conn.fetchrow(
        "SELECT image_proxy_s3_object_url FROM assets WHERE image_creation_date = CURRENT_DATE AND image_proxy_s3_object_url IS NOT NULL LIMIT 1"
    )
    return row["image_proxy_s3_object_url"] if row else DEFAULT_FALLBACK_IMAGE

# Use an async fallback function:
async def fallback_image_handler(conn: asyncpg.Connection) -> str:
    """
    Always returns the default fallback image URL. 
    Defined as async so we can 'await' it just like the other handlers.
    """
    return DEFAULT_FALLBACK_IMAGE

# Dictionary-based dynamic function routing for channels
# Note the type hint: Callable[[asyncpg.Connection], Awaitable[str]]
IMAGE_HANDLERS: dict[str, Callable[[asyncpg.Connection], Awaitable[str]]] = {
    "random": get_random_image_url,
    "daily": get_daily_image_url
}

# ------------------------------------------------------------------------------
# API Routes
# ------------------------------------------------------------------------------

router = APIRouter()

@router.post("/api/devices/{device_uuid}/display", response_model=DeviceDisplayResponse)
async def get_display(
    device_uuid: str, 
    request_data: DeviceDisplayRequest, 
    request: Request
) -> DeviceDisplayResponse:
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        device_row = await get_or_create_device(conn, device_uuid)
        device_id = device_row["id"]

        # Log device wake-up event
        await log_event(conn, device_id, "wake", f"current_fw_ver={request_data.current_fw_ver}")

        # Retrieve channel information
        channel_id = device_row.get("channel_id")
        channel_key = None
        if channel_id is not None:
            channel_row = await conn.fetchrow("SELECT channel_key FROM channels WHERE id = $1", channel_id)
            if channel_row:
                channel_key = channel_row["channel_key"]

        # Dynamically select the appropriate image function or fallback
        image_handler = IMAGE_HANDLERS.get(channel_key, fallback_image_handler)
        image_url = await image_handler(conn)

        # Ensure a valid image_url is returned
        if not image_url:
            image_url = DEFAULT_FALLBACK_IMAGE

        return DeviceDisplayResponse(image_url=image_url, next_wake_secs=device_row.get("next_wake_secs", 3600))

# ------------------------------------------------------------------------------
# FastAPI Application Setup
# ------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Create and later close the asyncpg connection pool.
    """
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
    yield
    await app.state.pool.close()

app = FastAPI(title="Fridge Thing API", lifespan=lifespan)
app.include_router(router)

# ------------------------------------------------------------------------------
# Entry Point
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)