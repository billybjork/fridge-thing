import os
import random
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, Request, Response
from pydantic import BaseModel
import io
import aiohttp
import numpy as np
from PIL import Image, ImageOps
from fastapi.responses import Response as FastAPIResponse
from urllib.parse import urlencode

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

load_dotenv()

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL")

# S3 configuration (remove if not used)
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_REGION = os.getenv("S3_REGION")

# Server configuration
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))

# Default fallback image (used if no valid image is found)
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/test.bmp"

# Target resolution for display (Inkplate 6 Color)
TARGET_RESOLUTION = (600, 448)

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
# Fallback image handler
# ------------------------------------------------------------------------------

async def fallback_image_handler(conn: asyncpg.Connection) -> str:
    """
    Always returns the default fallback image URL.
    Defined as async so we can 'await' it just like the other handlers.
    """
    return DEFAULT_FALLBACK_IMAGE

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

        # For 'daily' and 'random' channels, use dedicated endpoints.
        if channel_key == "daily":
            image_url = str(request.url_for("convert_daily"))
        elif channel_key == "random":
            image_url = str(request.url_for("convert_random"))
        else:
            # For any other channel, fallback to the default handler.
            image_url = await fallback_image_handler(conn)
            convert_endpoint = str(request.url_for("convert_image"))
            image_url = convert_endpoint + "?" + urlencode({"url": image_url})

        return DeviceDisplayResponse(image_url=image_url, next_wake_secs=device_row.get("next_wake_secs", 3600))

@router.get("/api/convert", name="convert_image")
async def convert_image(url: str):
    """
    Given an image URL (originally JPG/other format), fetch the image,
    process it (rotate, resize, letterbox), and return as a BMP.
    """
    # Validate the URL parameter
    if not url:
        return Response("Missing URL parameter", status_code=400)

    # Fetch the original image asynchronously using aiohttp.
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    # If fetching fails, try the default fallback image
                    async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                        if fallback_resp.status != 200:
                            return Response("Unable to fetch fallback image", status_code=500)
                        image_bytes = await fallback_resp.read()
                else:
                    image_bytes = await resp.read()
    except Exception as e:
        # On exception, attempt to get the fallback image
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                image_bytes = await fallback_resp.read()

    # Process the image using PIL.
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        # If image processing fails, try the fallback image.
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                image_bytes = await fallback_resp.read()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Check orientation and rotate if vertical.
    if image.height > image.width:
        image = image.rotate(90, expand=True)

    # Resize the image while maintaining aspect ratio.
    image = ImageOps.contain(image, TARGET_RESOLUTION)

    # If the resized image doesn't exactly match the target resolution, apply letterboxing.
    if image.size != TARGET_RESOLUTION:
        image = fill_letterbox(image, *TARGET_RESOLUTION)

    # Save the processed image as BMP into a bytes buffer.
    output_buffer = io.BytesIO()
    image.save(output_buffer, format="BMP")
    bmp_data = output_buffer.getvalue()

    # Return the complete BMP bytes.
    return FastAPIResponse(content=bmp_data, media_type="image/bmp")

def fill_letterbox(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """
    Letterbox the image by adding sidebars filled with the average edge color.
    """
    # Get the current width and height of the image
    current_width, current_height = img.size

    # Calculate letterbox dimensions
    left_fill = (target_width - current_width) // 2
    right_fill = target_width - current_width - left_fill
    top_fill = (target_height - current_height) // 2
    bottom_fill = target_height - current_height - top_fill

    # Convert image to numpy array for processing
    img_np = np.array(img)

    # Calculate average colors for edges
    left_color = img_np[:, 0].mean(axis=0).astype(int)
    right_color = img_np[:, -1].mean(axis=0).astype(int)
    top_color = img_np[0, :].mean(axis=0).astype(int)
    bottom_color = img_np[-1, :].mean(axis=0).astype(int)

    # Create filled regions with average colors
    left_fill_array = np.tile(left_color, (current_height, left_fill, 1))
    right_fill_array = np.tile(right_color, (current_height, right_fill, 1))
    top_fill_array = np.tile(top_color, (top_fill, target_width, 1))
    bottom_fill_array = np.tile(bottom_color, (bottom_fill, target_width, 1))

    # Combine the arrays: left/right fills then top/bottom fills.
    img_np = np.hstack([left_fill_array, img_np, right_fill_array])
    img_np = np.vstack([top_fill_array, img_np, bottom_fill_array])

    # Convert back to a PIL Image
    return Image.fromarray(img_np.astype('uint8'))

# ------------------------------------------------------------------------------
# FastAPI Application Setup
# ------------------------------------------------------------------------------

# Import channel routers from the subfolder.
from channels.daily_channel import router as daily_router
from channels.random_channel import router as random_router

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
app.include_router(daily_router)
app.include_router(random_router)

# ------------------------------------------------------------------------------
# Entry Point
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)