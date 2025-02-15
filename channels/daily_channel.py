import os
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
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/21977917882_ffae88748b_o.bmp"

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

async def check_image_displayed_recently(conn: asyncpg.Connection, uuid_val: str, device_uuid: str, threshold_date: datetime.date):
    """
    Check whether the image with uuid_val has been displayed on or after threshold_date
    for the specific device_uuid.
    (Assumes a display_logs table exists.)
    """
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS cnt 
        FROM display_logs
        WHERE uuid = $1 AND device_uuid = $2 AND display_date >= $3
        """,
        str(uuid_val),  # Convert UUID to string
        str(device_uuid),
        threshold_date,
    )
    return row["cnt"] > 0

async def find_eligible_images_for_date(conn: asyncpg.Connection, month_day: str, device_uuid: str):
    """
    For a given month_day, return up to IMAGE_FALLBACK_LIMIT images that haven't been
    shown recently on the specified device.
    """
    images = await query_images_by_month_day(conn, month_day)
    threshold_date = (datetime.now() - timedelta(days=IMAGE_REPEAT_THRESHOLD)).date()
    eligible = []
    for img in images:
        if not await check_image_displayed_recently(conn, img["uuid"], device_uuid, threshold_date):
            eligible.append(img)
        if len(eligible) >= IMAGE_FALLBACK_LIMIT:
            break
    return eligible

async def find_images_for_today_and_fallback(conn: asyncpg.Connection, device_uuid: str):
    """
    Attempt to find images for today's date (by month-day) that haven't been displayed
    recently on the specific device.
      - If found, return all images for that date.
      - If no images for today, fallback to previous days (up to IMAGE_FALLBACK_SEARCH_DAYS)
        and return images not displayed recently.
    Returns a tuple (list_of_images, fallback_used_bool).
    """
    today = datetime.now()
    today_md = today.strftime("%m-%d")
    today_images = await query_images_by_month_day(conn, today_md)

    if today_images:
        return today_images, False

    # Fallback: look back for a number of days
    for i in range(1, IMAGE_FALLBACK_SEARCH_DAYS + 1):
        fallback_date = today - timedelta(days=i)
        fallback_md = fallback_date.strftime("%m-%d")
        fallback_images = await find_eligible_images_for_date(conn, fallback_md, device_uuid)
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

def format_date_ordinal(date_obj: datetime) -> str:
    """
    Convert a datetime object to a string like "January 1st, 2023",
    adding the correct ordinal suffix.
    """
    day = date_obj.day
    if 11 <= day % 100 <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    return f"{date_obj.strftime('%B')} {day}{suffix}, {date_obj.year}"

def overlay_date_text(image, date_obj: datetime, fallback_used: bool) -> 'Image.Image':
    """
    Overlay the formatted date text on the image.
    Displays the month/day in the bottom-right (using a larger font)
    and a “years ago…” text in the top-left (using a smaller font).

    If fallback_used is True, an asterisk is added to today's date.
    """
    draw = ImageDraw.Draw(image)
    margin = 10

    script_dir = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(script_dir, "..", "fonts", "EBGaramond12-Regular.otf")

    # Define font sizes
    month_day_font_size = 60
    years_ago_font_size = 40

    try:
        month_day_font = ImageFont.truetype(font_path, month_day_font_size)
        years_ago_font = ImageFont.truetype(font_path, years_ago_font_size)
    except Exception as e:
        print(f"Error loading custom font: {e}. Falling back to default font.")
        month_day_font = ImageFont.load_default()
        years_ago_font = ImageFont.load_default()

    # Determine the texts based on whether fallback is in use.
    if fallback_used:
        today = datetime.now()
        formatted_date = format_date_ordinal(today)
        formatted_date = f"*{formatted_date}"
        current_year = today.year
        years_diff = current_year - date_obj.year
    else:
        formatted_date = format_date_ordinal(date_obj)
        current_year = datetime.now().year
        years_diff = current_year - date_obj.year

    # For the month/day text, take everything before the comma.
    if ", " in formatted_date:
        month_day_text = formatted_date.split(",")[0]
    else:
        month_day_text = formatted_date

    # Prepare the "years ago" text.
    years_ago_text = f"{years_diff} years ago..." if years_diff > 1 else "Last year..."

    # Calculate bounding boxes for placement.
    bbox_md = draw.textbbox((0, 0), month_day_text, font=month_day_font)
    md_width = bbox_md[2] - bbox_md[0]
    md_height = bbox_md[3] - bbox_md[1]

    bbox_ya = draw.textbbox((0, 0), years_ago_text, font=years_ago_font)
    ya_width = bbox_ya[2] - bbox_ya[0]
    ya_height = bbox_ya[3] - bbox_ya[1]

    # Position the month/day text in the bottom-right.
    x_md = image.width - md_width - margin
    y_md = image.height - md_height - margin

    # Position the years-ago text in the top-left.
    x_ya = margin
    y_ya = margin

    # Dynamically choose text color based on image brightness ---
    # Convert the image to grayscale and compute its average brightness.
    grayscale = image.convert("L")
    avg_brightness = np.mean(np.array(grayscale))
    # If the image is dark, use white text; otherwise, use black text.
    text_color = "white" if avg_brightness < 128 else "black"
    # ---------------------------------------------------------------------------

    # Draw the texts on the image.
    draw.text((x_md, y_md), month_day_text, fill=text_color, font=month_day_font)
    draw.text((x_ya, y_ya), years_ago_text, fill=text_color, font=years_ago_font)

    return image

async def process_daily_image(conn: asyncpg.Connection, device_uuid: str = "0") -> bytes:
    """
    Use the advanced daily logic to pick a daily image (with fallback if needed),
    fetch it, overlay the date text, log the display event, and return the BMP image bytes.
    """
    images, fallback_used = await find_images_for_today_and_fallback(conn, device_uuid)
    if not images:
        image_url = DEFAULT_FALLBACK_IMAGE
        image_date = datetime.now()
        image_uuid = None  # Nothing to log in this case.
    else:
        chosen = random.choice(images)
        image_url = chosen["image_proxy_s3_object_url"]
        image_date = chosen["image_creation_date"]
        image_uuid = chosen["uuid"]

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

    # Log the image display if we have a uuid.
    if image_uuid:
        await log_image_displayed(conn, image_uuid, device_uuid)
    
    return output_buffer.getvalue()

async def log_image_displayed(conn: asyncpg.Connection, uuid_val: str, device_uuid: str = "0"):
    """
    Log that an image was displayed by inserting a record into display_logs.
    device_uuid defaults to "0" if not provided.
    """
    display_date = datetime.now().date()
    # Convert uuid_val to string before passing it in.
    await conn.execute(
        """
        INSERT INTO display_logs (uuid, display_date, device_uuid)
        VALUES ($1, $2, $3)
        """,
        str(uuid_val), display_date, device_uuid,
    )

@router.get("/api/daily_convert", name="convert_daily")
async def convert_daily(request: Request, device_uuid: str = "0"):
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        bmp_data = await process_daily_image(conn, device_uuid)
    return Response(content=bmp_data, media_type="image/bmp")