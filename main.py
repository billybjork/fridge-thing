# Start dev server: python -m main

import os
import random
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, Request
from pydantic import BaseModel

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

# Load environment variables from a .env file (if available)
load_dotenv()

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# S3 configuration
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_REGION = os.getenv("S3_REGION")

# Server configuration
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))


# ------------------------------------------------------------------------------
# Database CRUD Operations
# ------------------------------------------------------------------------------

async def get_or_create_device(conn: asyncpg.Connection, device_uuid: str) -> dict:
    """
    Retrieve a device by its UUID or create a new device record if it doesn't exist.
    """
    row = await conn.fetchrow(
        "SELECT * FROM devices WHERE device_uuid = $1",
        device_uuid
    )
    if not row:
        device_id = await conn.fetchval(
            "INSERT INTO devices (device_uuid) VALUES ($1) RETURNING id",
            device_uuid
        )
        return {
            "id": device_id,
            "device_uuid": device_uuid,
            "next_wake_secs": 3600,
            "image_url": None
        }
    return dict(row)

async def update_device_image(conn: asyncpg.Connection, device_id: int, image_url: str) -> None:
    """
    Update the image URL for a given device.
    """
    await conn.execute(
        "UPDATE devices SET image_url = $1, updated_at = now() WHERE id = $2",
        image_url, device_id
    )

async def log_event(conn: asyncpg.Connection, device_id: int, event_type: str, message: str = "") -> None:
    """
    Log an event for a device.
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
    device_uuid: str
    current_fw_ver: Optional[str] = None  # OTA updates ignored for now
    battery_voltage: Optional[float] = None
    wifi_signal: Optional[int] = None

class DeviceDisplayResponse(BaseModel):
    image_url: str
    next_wake_secs: int
    # Future: add firmware update information here if needed


# ------------------------------------------------------------------------------
# Channel Operations
# ------------------------------------------------------------------------------

async def get_random_image_url(conn: asyncpg.Connection) -> str:
    """
    Retrieve a random image URL from the assets table.
    """
    row = await conn.fetchrow(
        """
        SELECT image_s3_object_url
        FROM assets
        ORDER BY random()
        LIMIT 1
        """
    )
    if row:
        return row["image_s3_object_url"]
    # Fallback URL if no image is found
    return "https://some-default-url.bmp"


# ------------------------------------------------------------------------------
# API Routes
# ------------------------------------------------------------------------------

router = APIRouter()

@router.post("/api/display", response_model=DeviceDisplayResponse)
async def get_display(request_data: DeviceDisplayRequest, request: Request) -> DeviceDisplayResponse:
    """
    Endpoint to retrieve the current display information for a device.
    """
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        device_row = await get_or_create_device(conn, request_data.device_uuid)
        device_id = device_row["id"]

        # Log the device wake event
        await log_event(conn, device_id, "wake", f"current_fw_ver={request_data.current_fw_ver}")

        # Use the stored image URL or fall back to a default URL
        image_url = device_row.get("image_url") or (
            "https://s3.us-west-1.amazonaws.com/bjork.love/test.bmp?"
            "response-content-disposition=inline&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&"
            "X-Amz-Security-Token=IQoJb3JpZ2luX2VjEMz%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMSJGMEQCIA%2BozLOLcON5f%2B3vcTJDgNJ9DxsSi6Ov0CIDcfhy1%2BPSAiB%2BGGz7j4nj35cr52Pixi9Jiz%2BO%2FuZ%2FVqlP2wH5xX33iirUAwjl%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8BEAAaDDk3NTA0OTg4NzI5NSIMaoM%2FeUoWinEerrm1KqgDgwcdmDnZFkwiMoZwGPpGkgwWoAf%2FdSWxylkCUNKvtvTfiZKofGPc3NGnYTxEgKziJmGKzsM7wNODfo52BRbcbTNJjzXAczqm5JExysH%2FwVB1HmzQOxpJtHAJyJ7DOLVstuUkT4WoFqmUodyYJg3fkpEKUgmhAUXLer2BKBbd4SxmkgI7GB7jW8sf1zwyhHroxRB9slEG9vDa3wLD4XV2CTFLDWf5%2B3fDRMpVhs8gu2sOA8oL0cfb%2FbIQqrUvUO2f0O2er%2FUCnHO1w%2BdQpmNjc3E0EXHzCgK3H2eTR0zw6lE7HE%2FQlPc1kbhANIfBGRhJSnwpc%2Fr2IqxvAkRazL1kDkJkFjf7aMstgPVUEcYhulxAz5OX2KbrzWo0naT069jZ4hoDTQ%2FftclSwv63kZjixnyt1DjVVTN0f4xzVskU0rZ5yH8iuBdBUPxe2ufQ%2FIG4mUZoEmY7J4VuPBoNYnnUt20GgJ8ltkfDm2aXXCWSzAzRpER%2FeBqX9rpnswBbqq1c9NXHQyMP6iigeBAqGLuqHIQMVAMu9CmEO7z4TGgC3W3qXGnwqbRnpDCN8K%2B9BjrlAjgbqCNvr2XxPUYjZ%2FxDEViEAcMWZz2WtT8617vBcf6BBD4uszlx5P6OeIzPfAlzEvBlWubG7i8cuTZFVlYcFYG%2BMjK6pNjHaBIrRQlkdEkmm%2FmPcLKCpBou41haMxXM1sDx0ra7oy7Ivg2qmcTG8TGMZ6JeN7mXhREws36U1dvDw5U57AaPduTRa1Qxqzsx2VoeyOtNJvQRVpBEkEmtzFbw1ewvHoy%2Fjo0SZP13cWehWgSy5E4rBeEBntsNL78xv1enNjOPJ7jgl%2BrFUgEW95HxF%2B9fLX3KxGqM%2BKjbaL1xkT0SlLMJBisLonG%2FmjCR4jn5wquL6Ru%2BvFRFiUXfhIBzGrfGWL4u0C01oJBuBpB%2BkOlKsA6xEXjvHj%2FpwBbM9IWrrr6DZhN7VDmEaClhrWCgsL6E4WxQsqm2n1M1aehbefLeMoXkjoMdxAuqDV1aBRI7zATrrsVQEHVpZNb5eGRfrXCJZQ%3D%3D"
        )
        next_wake_secs = device_row.get("next_wake_secs", 3600)

        return DeviceDisplayResponse(
            image_url=image_url,
            next_wake_secs=next_wake_secs
        )

@router.post("/api/devices/{device_uuid}/refresh")
async def refresh_device(device_uuid: str, request: Request) -> dict:
    """
    Endpoint to refresh a device's image by picking a random one from the assets.
    """
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        device_row = await get_or_create_device(conn, device_uuid)
        device_id = device_row["id"]

        # Retrieve a random image URL and update the device record
        random_url = await get_random_image_url(conn)
        await update_device_image(conn, device_id, random_url)

        return {"status": "ok", "image_url": random_url}


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