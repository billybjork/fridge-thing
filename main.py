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

# S3 configuration (keep them if you eventually need to generate S3 pre-signed URLs, etc.)
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_REGION = os.getenv("S3_REGION")

# Server configuration
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))

# Default fallback image (used if no valid image is found)
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/test.bmp?response-content-disposition=inline&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEOD%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMSJHMEUCIFsViuSDfgIsHjBE%2BZ2jhCVM0dNoNVCTK5vluviD%2FCYaAiEAyJQsQV7pqFS7d2cYT0wtOJ%2BzDKrxugZtO%2FbtY7n1L1Yq1AMI%2Bf%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FARAAGgw5NzUwNDk4ODcyOTUiDKtYr4c0ikZgyhU%2FHSqoA6HvikuckRfqtHAEhekSaLX27qOsasyrtLWtWTFwIjc3E%2FJARuLZIvrcbMacNCvEANAUZyW4HLAy3PuZkNO%2FZKsRfBbdpqLKDTAhpybXF2uKKp%2B2DYI9%2B2NLdmoGjN2xzWoTx2g5Wnxyu8uPlqMAp3fLWG1jp%2FyUFB8gvcI6PxbSi%2FiERVb5ELMjenjEHWyGEl0UqqT2DSsYpDMrZyhX1w13lm3xsOKC9Z5eBUzBBUAJOgyH5BlRGXI%2Bd47LDd3UsPXJAHSp5kKU%2BAd1Ldboz0ioyyxwza%2F438dn1soxMhWnKdRwzv1kjwds0odmi2xeK%2FK%2FZtOhFMhXzp0LygT4o0IPf7YokfJcl91RC3XJXNzvCG6XUulIlqEn5pPoZ35ZP6N3rhhc05Z4UAfQXs9Fi2kGz%2Bfsgpdi2pwHylse3Jm47uT8gL6R6Zo9WMIEhuDpfu0uXr0zjlwSgsSxxmvDKeC8YLzJcMkSEv03Gu2ORGjZp7VVS8pghQBdTy6MBSmUEt0jzqMYJ%2Fw2xiqPa0gZ%2FER%2BibMs1sWMzl5pvIUK6glDmOL%2BYavmqgYwyuy0vQY65AJmapUrThj3kR42U%2BxNq%2B%2FzzsLRJaxgsMr1Zv%2BhNG9aWc39lbdW18W160kNXYvVIZj0hTeS0im3ulZPEeYLAM4QqqHNFoPveNVUMnmLTYSFTSicDObvPYu9268jgaraV4XfVxJpeeu6XRLWsaK7sdUD5nVQ0Sama5UjrpvHYcL4aMadf%2BaT7zDeK8jM2bu8%2FI27zIMQgRuqEOQwdWLoMZe9Yk%2B6orVhaIDbBaZ65shiEoMEVdE5SPYfXpUL6XnKOx1YpaAYMK%2FGNoLn%2BldWYS44aJOIGv%2BEHAOV%2F7d10UEejN3VwrgBjgbU5vPCD8%2FsOoVCIvSh8Gq6L0htaY9en8Tndy02Ip5Gf1Ouzy2JM%2FgPtRSRWa5PzN7NJP5EDwJbmPmtFBFq6LfwQlRdOlY7pQ5oUMtMSG3n2LnL%2FPXiHskZr7au2wX3nN9xFl39e3BiF%2FhgKe5%2B9p1AtstvTJeGIRsxOnPe7A%3D%3D&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIA6GBMARY7ZABTBFK6%2F20250213%2Fus-west-1%2Fs3%2Faws4_request&X-Amz-Date=20250213T001750Z&X-Amz-Expires=43200&X-Amz-SignedHeaders=host&X-Amz-Signature=1504126dac652227b997831341f98f2e44ca91ae1d9cac4fbc8504d3d17ae491"

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
    """
    row = await conn.fetchrow(
        "SELECT image_s3_object_url FROM assets ORDER BY random() LIMIT 1"
    )
    return row["image_s3_object_url"] if row else DEFAULT_FALLBACK_IMAGE

async def get_daily_image_url(conn: asyncpg.Connection) -> str:
    """
    Select the 'daily' image from the assets table.
    """
    row = await conn.fetchrow(
        "SELECT image_s3_object_url FROM assets WHERE image_creation_date = CURRENT_DATE LIMIT 1"
    )
    return row["image_s3_object_url"] if row else DEFAULT_FALLBACK_IMAGE

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
    """
    Single endpoint that provides the correct image for a device based on its assigned channel.
    """
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        # Retrieve or create device entry
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

        # Always call it with 'await' because it's guaranteed to be async now
        image_url = await image_handler(conn)

        # Get the next wake-up time
        next_wake_secs = device_row.get("next_wake_secs", 3600)

        return DeviceDisplayResponse(image_url=image_url, next_wake_secs=next_wake_secs)

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