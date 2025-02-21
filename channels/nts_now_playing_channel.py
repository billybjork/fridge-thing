import os
import io
import aiohttp
from fastapi import APIRouter, Request, Response
from PIL import Image, ImageOps
from image_utils import fill_letterbox

# ----------------------------------------------------------------------
# Configuration for NTS Now Playing Channel
# ----------------------------------------------------------------------
TARGET_RESOLUTION = (600, 448)
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/21977917882_ffae88748b_o.bmp"

router = APIRouter()

@router.get("/api/nts_now_playing_convert", name="convert_nts_now_playing")
async def convert_nts_now_playing(request: Request, device_uuid: str = "0"):
    """
    Fetch a screenshot of the NTS webpage and process it for display.
    For now, this endpoint uses an external screenshot service (thum.io) to capture https://www.nts.live/
    and applies the standard processing (rotate, resize, letterbox) to produce a BMP image.
    """
    # URL of screenshot service capturing the NTS live page.
    screenshot_url = "https://image.thum.io/get/png/https://www.nts.live/?fresh=true"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(screenshot_url) as resp:
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
    return Response(content=bmp_data, media_type="image/bmp")