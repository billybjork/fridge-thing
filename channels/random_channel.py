import io
import random
import aiohttp
import asyncpg
import numpy as np
from fastapi import APIRouter, Request, Response
from PIL import Image, ImageOps

router = APIRouter()

# ==============================================================================
# Random Channel Logic
# ==============================================================================

# ----- Configuration for Random Channel -----
TARGET_RESOLUTION = (600, 448)
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/test.bmp"

async def get_random_image_url(conn: asyncpg.Connection) -> str:
    """
    Select a random image from the assets table.
    Ensures a valid URL is always returned.
    """
    row = await conn.fetchrow(
        "SELECT image_proxy_s3_object_url FROM assets WHERE image_proxy_s3_object_url IS NOT NULL ORDER BY random() LIMIT 1"
    )
    return row["image_proxy_s3_object_url"] if row else DEFAULT_FALLBACK_IMAGE

async def process_random_image(conn: asyncpg.Connection) -> bytes:
    """
    Use the random channel logic to select a random image,
    fetch it, process it (rotate, resize, letterbox), and return BMP image bytes.
    """
    image_url = await get_random_image_url(conn)

    # Fetch the image using aiohttp
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
    image = ImageOps.contain(image, TARGET_RESOLUTION)

    # If the resized image doesn't exactly match the target resolution, apply letterboxing.
    if image.size != TARGET_RESOLUTION:
        image = fill_letterbox(image, TARGET_RESOLUTION[0], TARGET_RESOLUTION[1])

    # Convert to BMP bytes.
    output_buffer = io.BytesIO()
    image.save(output_buffer, format="BMP")
    return output_buffer.getvalue()

def fill_letterbox(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """
    Letterbox the image by adding sidebars filled with the average edge color.
    """
    current_width, current_height = img.size

    left_fill = (target_width - current_width) // 2
    right_fill = target_width - current_width - left_fill
    top_fill = (target_height - current_height) // 2
    bottom_fill = target_height - current_height - top_fill

    img_np = np.array(img)
    left_color = img_np[:, 0].mean(axis=0).astype(int)
    right_color = img_np[:, -1].mean(axis=0).astype(int)
    top_color = img_np[0, :].mean(axis=0).astype(int)
    bottom_color = img_np[-1, :].mean(axis=0).astype(int)

    left_fill_array = np.tile(left_color, (current_height, left_fill, 1))
    right_fill_array = np.tile(right_color, (current_height, right_fill, 1))
    top_fill_array = np.tile(top_color, (top_fill, target_width, 1))
    bottom_fill_array = np.tile(bottom_color, (bottom_fill, target_width, 1))

    img_np = np.hstack([left_fill_array, img_np, right_fill_array])
    img_np = np.vstack([top_fill_array, img_np, bottom_fill_array])
    return Image.fromarray(img_np.astype('uint8'))

@router.get("/api/random_convert", name="convert_random")
async def convert_random(request: Request):
    """
    Endpoint that uses the random channel logic to produce a BMP image.
    """
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        bmp_data = await process_random_image(conn)
    return Response(content=bmp_data, media_type="image/bmp")
