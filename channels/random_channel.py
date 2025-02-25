import io
import random
import aiohttp
import asyncpg
import numpy as np
from fastapi import APIRouter, Request, Response
from PIL import Image, ImageOps

from image_utils import fill_letterbox

router = APIRouter()

# ==============================================================================
# Random Channel Logic
# ==============================================================================

DEFAULT_WIDTH = 600
DEFAULT_HEIGHT = 448
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/21977917882_ffae88748b_o.bmp"

async def get_random_image_url(conn: asyncpg.Connection) -> str:
    """
    Select a random image from the assets table.
    Ensures a valid URL is always returned.
    """
    row = await conn.fetchrow(
        "SELECT image_proxy_s3_object_url FROM assets WHERE image_proxy_s3_object_url IS NOT NULL ORDER BY random() LIMIT 1"
    )
    return row["image_proxy_s3_object_url"] if row else DEFAULT_FALLBACK_IMAGE

async def process_random_image(conn: asyncpg.Connection, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> bytes:
    """
    Use the random channel logic to select a random image,
    fetch it, process it (rotate, resize, letterbox), and return BMP image bytes.
    The image is resized and letterboxed to the provided width and height.
    """
    image_url = await get_random_image_url(conn)

    # Fetch the image using aiohttp.
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as resp:
            if resp.status != 200:
                async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                    image_bytes = await fallback_resp.read()
            else:
                image_bytes = await resp.read()

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                image_bytes = await fallback_resp.read()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Check orientation and rotate if vertical.
    if image.height > image.width:
        image = image.rotate(90, expand=True)

    # Resize the image while maintaining aspect ratio.
    image = ImageOps.contain(image, (width, height))

    # If the resized image doesn't exactly match the target resolution, apply letterboxing.
    if image.size != (width, height):
        image = fill_letterbox(image, width, height)

    # Convert to BMP bytes.
    output_buffer = io.BytesIO()
    image.save(output_buffer, format="BMP")
    return output_buffer.getvalue()

@router.get("/api/random_convert", name="convert_random")
async def convert_random(request: Request, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT):
    """
    Endpoint that uses the random channel logic to produce a BMP image.
    Accepts optional query parameters 'width' and 'height' to dynamically adapt the image.
    """
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        bmp_data = await process_random_image(conn, width, height)
    return Response(content=bmp_data, media_type="image/bmp")