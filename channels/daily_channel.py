import io
import random
from datetime import datetime, timedelta

import aiohttp
import asyncpg
import numpy as np
from fastapi import APIRouter, Request, Response
from PIL import Image, ImageOps, ImageDraw, ImageFont

router = APIRouter()

# ==============================================================================
# Daily Channel Logic
# ==============================================================================

# ----- Configuration for Daily Channel -----
TARGET_RESOLUTION = (600, 448)
IMAGE_REPEAT_THRESHOLD = 10       # in days
IMAGE_FALLBACK_SEARCH_DAYS = 30     # how many days back we look for fallback images
IMAGE_FALLBACK_LIMIT = 5            # how many images we pick for fallback scenario
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/test.bmp"

# ----- Database Query Functions for Daily Channel -----

async def query_images_by_month_day(conn: asyncpg.Connection, month_day: str):
    """
    Query all images by the specified month_day in 'MM-DD' format.
    Returns rows with image_proxy_s3_object_url, uuid, and image_creation_date.
    """
    rows = await conn.fetch(
        """
        SELECT image_proxy_s3_object_url, uuid, image_creation_date
        FROM assets
        WHERE to_char(image_creation_date, 'MM-DD') = $1
          AND image_proxy_s3_object_url IS NOT NULL
        ORDER BY image_creation_date DESC
        """,
        month_day,
    )
    return rows

async def check_image_displayed_recently(conn: asyncpg.Connection, uuid_val: str, threshold_date: datetime.date):
    """
    Check whether the image with uuid_val has been displayed on or after threshold_date.
    (Assumes a display_logs table exists.)
    """
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS cnt 
        FROM display_logs
        WHERE uuid = $1 AND display_date >= $2
        """,
        uuid_val,
        threshold_date,
    )
    return row["cnt"] > 0

async def find_eligible_images_for_date(conn: asyncpg.Connection, month_day: str):
    """
    For a given month_day, return up to IMAGE_FALLBACK_LIMIT images that haven't been
    shown recently.
    """
    images = await query_images_by_month_day(conn, month_day)
    threshold_date = (datetime.now() - timedelta(days=IMAGE_REPEAT_THRESHOLD)).date()
    eligible = []
    for img in images:
        if not await check_image_displayed_recently(conn, img["uuid"], threshold_date):
            eligible.append(img)
        if len(eligible) >= IMAGE_FALLBACK_LIMIT:
            break
    return eligible

async def find_images_for_today_and_fallback(conn: asyncpg.Connection):
    """
    Attempt to find images for today's date (by month-day).
      - If found, return all images for that date.
      - If no images for today, fallback to previous days (up to IMAGE_FALLBACK_SEARCH_DAYS)
        and return images not displayed recently.
    Returns a tuple (list_of_images, fallback_used_bool).
    """
    today = datetime.now()
    today_md = today.strftime("%m-%d")
    today_images = await query_images_by_month_day(conn, today_md)

    print(f"DEBUG: Today's date format -> {today_md}")
    print(f"DEBUG: Found {len(today_images)} images for today.")

    if today_images:
        return today_images, False

    # Fallback: look back for a number of days
    for i in range(1, IMAGE_FALLBACK_SEARCH_DAYS + 1):
        fallback_date = today - timedelta(days=i)
        fallback_md = fallback_date.strftime("%m-%d")
        fallback_images = await find_eligible_images_for_date(conn, fallback_md)
        if fallback_images:
            random.shuffle(fallback_images)
            return fallback_images, True

    return [], False

# ----- Image Processing Functions for Daily Channel -----

def fill_letterbox(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """
    Letterbox the image to exactly target_width x target_height using average edge colors.
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
    return Image.fromarray(img_np.astype("uint8"))

def overlay_date_text(image: Image.Image, date_obj: datetime, fallback_used: bool) -> Image.Image:
    """
    Overlay the date text on the image.
    Draws the month/day at the bottom right and the year at the top left.
    """
    draw = ImageDraw.Draw(image)
    margin = 10

    # Use default font; you can specify a TTF file if desired.
    month_day_text = date_obj.strftime("%B %d")
    year_text = date_obj.strftime("%Y")
    font = ImageFont.load_default()

    # Get text bounding box for dimensions
    bbox_md = draw.textbbox((0, 0), month_day_text, font=font)
    md_width = bbox_md[2] - bbox_md[0]
    md_height = bbox_md[3] - bbox_md[1]

    bbox_year = draw.textbbox((0, 0), year_text, font=font)
    year_width = bbox_year[2] - bbox_year[0]
    year_height = bbox_year[3] - bbox_year[1]

    # Calculate positions
    x_md = image.width - md_width - margin
    y_md = image.height - md_height - margin

    x_year = margin
    y_year = margin

    # Draw text on the image
    draw.text((x_md, y_md), month_day_text, fill="black", font=font)
    draw.text((x_year, y_year), year_text, fill="black", font=font)
    
    return image

async def process_daily_image(conn: asyncpg.Connection) -> bytes:
    """
    Use the advanced daily logic to pick a daily image (with fallback if needed),
    fetch it, overlay the date text, and return the BMP image bytes.
    """
    images, fallback_used = await find_images_for_today_and_fallback(conn)
    if not images:
        image_url = DEFAULT_FALLBACK_IMAGE
        image_date = datetime.now()
    else:
        chosen = random.choice(images)
        image_url = chosen["image_proxy_s3_object_url"]
        image_date = chosen["image_creation_date"]

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
        image = Image.new("RGB", TARGET_RESOLUTION, (255, 255, 255))

    # Resize and letterbox.
    image = ImageOps.contain(image, TARGET_RESOLUTION)
    if image.size != TARGET_RESOLUTION:
        image = fill_letterbox(image, TARGET_RESOLUTION[0], TARGET_RESOLUTION[1])

    # Overlay date text.
    image = overlay_date_text(image, image_date, fallback_used)

    # Convert to BMP bytes.
    output_buffer = io.BytesIO()
    image.save(output_buffer, format="BMP")
    return output_buffer.getvalue()

@router.get("/api/daily_convert", name="convert_daily")
async def convert_daily(request: Request):
    """
    Endpoint that uses the advanced daily logic to produce a BMP image.
    """
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        bmp_data = await process_daily_image(conn)
    return Response(content=bmp_data, media_type="image/bmp")