import os
import random
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

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
from datetime import datetime, time, timedelta
import pytz
import json

from utils.image_utils import fill_letterbox

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

load_dotenv()

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL")

# Server configuration
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))

# Default fallback image (used if no valid image is found)
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/21977917882_ffae88748b_o.bmp"

# Default target resolution (for legacy devices)
TARGET_RESOLUTION = (600, 448)

# Timezone for time synchronization
SERVER_TIMEZONE = pytz.timezone("America/Chicago")

# ------------------------------------------------------------------------------
# Database Operations
# ------------------------------------------------------------------------------

async def get_or_create_device(conn: asyncpg.Connection, device_uuid: str) -> dict:
    """
    Retrieve or create a device entry. Returns device data including channel_id and display resolution.
    If a new device is created, default resolution is set to TARGET_RESOLUTION.
    """
    row = await conn.fetchrow(
        "SELECT * FROM devices WHERE device_uuid = $1", device_uuid
    )
    if not row:
        device_id = await conn.fetchval(
            "INSERT INTO devices (device_uuid, display_width, display_height) VALUES ($1, $2, $3) RETURNING id",
            device_uuid, TARGET_RESOLUTION[0], TARGET_RESOLUTION[1]
        )
        return {
            "id": device_id,
            "device_uuid": device_uuid,
            "channel_id": None,
            "next_wake_secs": 3600,
            "display_width": TARGET_RESOLUTION[0],
            "display_height": TARGET_RESOLUTION[1],
            "image_url": None
        }
    return dict(row)

# ------------------------------------------------------------------------------
# Time Synchronization Utilities
# ------------------------------------------------------------------------------

def get_current_time_info(timezone=None) -> dict:
    """
    Get current time information formatted for the device's RTC.
    Returns a dictionary with the current time in the specified timezone.
    If no timezone is provided, uses SERVER_TIMEZONE.
    """
    if timezone is None:
        timezone = SERVER_TIMEZONE
    
    now = datetime.now(timezone)
    
    # Convert to 2-digit year
    year = now.year % 100
    
    # Get weekday as 1-7 (Monday=1, Sunday=7)
    weekday = now.isoweekday()  # isoweekday returns 1-7 where 1=Monday
    
    return {
        "year": year,
        "month": now.month,
        "day": now.day,
        "weekday": weekday,
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second
    }

# ------------------------------------------------------------------------------
# Fallback image handler
# ------------------------------------------------------------------------------

async def fallback_image_handler(conn: asyncpg.Connection) -> str:
    """
    Always returns the default fallback image URL.
    """
    return DEFAULT_FALLBACK_IMAGE

# ------------------------------------------------------------------------------
# API Routes
# ------------------------------------------------------------------------------

router = APIRouter()

@router.post("/api/devices/{device_uuid}/display")
async def get_display(
    device_uuid: str, 
    request: Request
) -> dict:
    """
    Endpoint for device display requests.
    Retrieves device information (including display resolution) and returns an image URL
    pointing to a conversion endpoint that dynamically adapts the image.
    Also provides time information when requested for RTC synchronization.
    """
    # Parse request body as raw JSON
    try:
        body = await request.json()
        print(f"Received request from device {device_uuid}: {body}")
    except Exception as e:
        print(f"Error parsing request from device {device_uuid}: {str(e)}")
        return {"error": f"Invalid request body: {str(e)}"}
    
    # Extract time sync flag directly from JSON (default to true for testing)
    request_time_sync = body.get("request_time_sync", False)
    print(f"Device {device_uuid} requested time sync: {request_time_sync}")
    
    # Update device information in database if needed
    # TODO: Store battery level, firmware version, etc.
    
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        device_row = await get_or_create_device(conn, device_uuid)
        device_id = device_row["id"]

        # Use device-specific display resolution or fallback to default
        display_width = device_row.get("display_width") or TARGET_RESOLUTION[0]
        display_height = device_row.get("display_height") or TARGET_RESOLUTION[1]

        # ------------------------------------------------------------------------------
        # No-Refresh Period Check (Midnight to 8am CST)
        # ------------------------------------------------------------------------------
        cst = pytz.timezone("America/Chicago")
        now_cst = datetime.now(cst)
        
        # For Pacific Time
        pacific = pytz.timezone("America/Los_Angeles")
        now_pacific = datetime.now(pacific)
        print(f"Current time - CST: {now_cst}, Pacific: {now_pacific}")
        
        if now_cst.time() >= time(0, 0) and now_cst.time() < time(8, 0):
            # Calculate seconds until 8:00 am CST
            target_time = datetime.combine(now_cst.date(), time(8, 0), tzinfo=cst)
            if now_cst >= target_time:
                target_time += timedelta(days=1)
            next_wake_secs = int((target_time - now_cst).total_seconds())
            
            # Create response with or without time info
            response = {
                "image_url": "NO_REFRESH", 
                "next_wake_secs": next_wake_secs
            }
            
            # Add time information if requested or always for testing
            response["time"] = get_current_time_info(pacific)  # Use Pacific time
            print(f"Sending time info to device {device_uuid}: {response['time']}")
                
            return response

        # Retrieve channel information
        channel_id = device_row.get("channel_id")
        channel_key = None
        if channel_id is not None:
            channel_row = await conn.fetchrow("SELECT channel_key FROM channels WHERE id = $1", channel_id)
            if channel_row:
                channel_key = channel_row["channel_key"]

        # Build common query parameters (resolution parameters)
        params = {"width": display_width, "height": display_height}

        # For dedicated channels, include device_uuid and resolution parameters
        if channel_key == "daily":
            params["device_uuid"] = device_uuid
            image_url = str(request.url_for("convert_daily")) + "?" + urlencode(params)
        elif channel_key == "random":
            params["device_uuid"] = device_uuid
            image_url = str(request.url_for("convert_random")) + "?" + urlencode(params)
        elif channel_key == "nts-now-playing":
            params["device_uuid"] = device_uuid
            image_url = str(request.url_for("convert_nts_now_playing")) + "?" + urlencode(params)
        else:
            # Fallback to default image conversion endpoint
            fallback_url = await fallback_image_handler(conn)
            params["url"] = fallback_url
            image_url = str(request.url_for("convert_image")) + "?" + urlencode(params)

        # Create response with or without time info
        response = {
            "image_url": image_url, 
            "next_wake_secs": device_row.get("next_wake_secs", 3600)
        }
        
        # Add time information if requested or always for testing
        response["time"] = get_current_time_info(pacific)  # Use Pacific time
        print(f"Sending time info to device {device_uuid}: {response['time']}")
            
        return response

@router.get("/api/convert", name="convert_image")
async def convert_image(url: str, width: int = None, height: int = None):
    """
    Converts an image from a given URL to a BMP image with the desired resolution.
    Accepts optional query parameters 'width' and 'height' to dynamically adjust the image.
    """
    # Use provided dimensions or fallback to default TARGET_RESOLUTION
    if width is None or height is None:
        width, height = TARGET_RESOLUTION

    # Validate the URL parameter
    if not url:
        return Response("Missing URL parameter", status_code=400)

    # Fetch the original image asynchronously using aiohttp.
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                        if fallback_resp.status != 200:
                            return Response("Unable to fetch fallback image", status_code=500)
                        image_bytes = await fallback_resp.read()
                else:
                    image_bytes = await resp.read()
    except Exception as e:
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                image_bytes = await fallback_resp.read()

    # Process the image using PIL.
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                image_bytes = await fallback_resp.read()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Check orientation and rotate if vertical.
    if image.height > image.width:
        image = image.rotate(90, expand=True)

    # Resize the image while maintaining aspect ratio.
    image = ImageOps.contain(image, (width, height))

    # Apply letterboxing if the resized image doesn't exactly match the target resolution.
    if image.size != (width, height):
        image = fill_letterbox(image, width, height)

    # Save the processed image as BMP into a bytes buffer.
    output_buffer = io.BytesIO()
    image.save(output_buffer, format="BMP")
    bmp_data = output_buffer.getvalue()

    return FastAPIResponse(content=bmp_data, media_type="image/bmp")

# ------------------------------------------------------------------------------
# FastAPI Application Setup
# ------------------------------------------------------------------------------

# Import channel routers from the subfolder.
from channels.daily_channel import router as daily_router
from channels.random_channel import router as random_router
from channels.nts_now_playing_channel import router as nts_router

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
app.include_router(nts_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)