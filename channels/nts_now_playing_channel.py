import os
import io
import aiohttp
import textwrap
from fastapi import APIRouter, Request, Response
from PIL import Image, ImageOps, ImageDraw, ImageFont
from bs4 import BeautifulSoup
from image_utils import fill_letterbox

# ----------------------------------------------------------------------
# Configuration for NTS Now Playing Channel
# ----------------------------------------------------------------------
TARGET_RESOLUTION = (600, 448)
DEFAULT_FALLBACK_IMAGE = "https://s3.us-west-1.amazonaws.com/bjork.love/21977917882_ffae88748b_o.bmp"
PAGE_URL = "https://www.nts.live/"

router = APIRouter()

def get_default_font(size):
    try:
        # Adjust the font path if needed
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()

async def fetch_image(session, url):
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None

def render_channel_block(base_image, channel, block_box):
    """
    Render the channel content into the given block_box on base_image.
    block_box is a tuple: (x, y, width, height)
    """
    draw = ImageDraw.Draw(base_image)
    x, y, w, h = block_box
    margin = 5
    current_y = y + margin

    # Draw the header text (extracted from the header element)
    header_font = get_default_font(18)
    header_text = channel.get("header", "")
    draw.text((x + margin, current_y), header_text, fill="black", font=header_font)
    current_y += 25  # allocate space for the header

    # If an image is available (extracted from the button's <img>), paste it
    if channel.get("image"):
        ch_img = channel["image"].copy()
        # Resize the image to fit into a 100x100 area
        ch_img.thumbnail((100, 100))
        base_image.paste(ch_img, (x + margin, current_y))
    # Define x position for the details text (to the right of the image if it exists)
    text_x = x + margin + 110 if channel.get("image") else x + margin
    text_width = w - (110 if channel.get("image") else 0) - 2 * margin

    # Draw the details text (extracted from the details element), wrapped as needed.
    details_font = get_default_font(14)
    details_text = channel.get("details", "")
    # Estimate a max character count per line (roughly) based on available width.
    max_chars = text_width // 7  
    wrapped_text = textwrap.fill(details_text, width=max_chars)
    draw.text((text_x, current_y), wrapped_text, fill="black", font=details_font)

@router.get("/api/nts_now_playing_convert", name="convert_nts_now_playing")
async def convert_nts_now_playing(request: Request, device_uuid: str = "0"):
    """
    Fetch the NTS.live homepage, extract the following elements:
      - For channel 1: 
          header:   "#nts-live-header > div.live-header__channels--expanded.live-header__channels > div:nth-child(1) > header"
          image:    "#nts-live-header > div.live-header__channels--expanded.live-header__channels > div:nth-child(1) > div.live-channel__content > div.live-channel__content__picture > button" (grab first <img>)
          details:  "#nts-live-header > div.live-header__channels--expanded.live-header__channels > div:nth-child(1) > div.live-channel__content > div.live-channel__content__details"
      - For channel 2:
          header:   "#nts-live-header > div.live-header__channels--expanded.live-header__channels > div.live-channel.channel-2 > header"
          image:    "#nts-live-header > div.live-header__channels--expanded.live-header__channels > div.live-channel.channel-2 > div.live-channel__content > div.live-channel__content__picture > button" (grab first <img>)
          details:  "#nts-live-header > div.live-header__channels--expanded.live-header__channels > div.live-channel.channel-2 > div.live-channel__content > div.live-channel__content__details"

    These extracted contents are reassembled to fit the 600x448 display.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PAGE_URL) as resp:
                if resp.status != 200:
                    raise Exception("Failed to fetch page")
                html_content = await resp.text()
    except Exception:
        # Fallback to default image if fetching fails.
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                data = await fallback_resp.read()
        fallback_img = Image.open(io.BytesIO(data)).convert("RGB")
        if fallback_img.height > fallback_img.width:
            fallback_img = fallback_img.rotate(90, expand=True)
        fallback_img = ImageOps.contain(fallback_img, TARGET_RESOLUTION)
        if fallback_img.size != TARGET_RESOLUTION:
            fallback_img = fill_letterbox(fallback_img, *TARGET_RESOLUTION)
        output_buffer = io.BytesIO()
        fallback_img.save(output_buffer, format="BMP")
        return Response(content=output_buffer.getvalue(), media_type="image/bmp")

    # Parse the HTML using BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    container = soup.select_one("#nts-live-header > div.live-header__channels--expanded.live-header__channels")
    if not container:
        # Fallback if the container is not found.
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                data = await fallback_resp.read()
        fallback_img = Image.open(io.BytesIO(data)).convert("RGB")
        if fallback_img.height > fallback_img.width:
            fallback_img = fallback_img.rotate(90, expand=True)
        fallback_img = ImageOps.contain(fallback_img, TARGET_RESOLUTION)
        if fallback_img.size != TARGET_RESOLUTION:
            fallback_img = fill_letterbox(fallback_img, *TARGET_RESOLUTION)
        output_buffer = io.BytesIO()
        fallback_img.save(output_buffer, format="BMP")
        return Response(content=output_buffer.getvalue(), media_type="image/bmp")

    channels = []

    # --- Channel 1 ---
    first_channel = container.select_one("div:nth-child(1)")
    if first_channel:
        header_el = first_channel.select_one("header")
        picture_el = first_channel.select_one("div.live-channel__content > div.live-channel__content__picture > button")
        details_el = first_channel.select_one("div.live-channel__content > div.live-channel__content__details")
        header_text = header_el.get_text(" ", strip=True) if header_el else ""
        details_text = details_el.get_text(" ", strip=True) if details_el else ""
        img_tag = picture_el.find("img") if picture_el else None
        img_url = img_tag["src"] if img_tag and img_tag.has_attr("src") else None
        channels.append({
            "header": header_text,
            "details": details_text,
            "image_url": img_url
        })

    # --- Channel 2 ---
    second_channel = container.select_one("div.live-channel.channel-2")
    if second_channel:
        header_el = second_channel.select_one("header")
        picture_el = second_channel.select_one("div.live-channel__content > div.live-channel__content__picture > button")
        details_el = second_channel.select_one("div.live-channel__content > div.live-channel__content__details")
        header_text = header_el.get_text(" ", strip=True) if header_el else ""
        details_text = details_el.get_text(" ", strip=True) if details_el else ""
        img_tag = picture_el.find("img") if picture_el else None
        img_url = img_tag["src"] if img_tag and img_tag.has_attr("src") else None
        channels.append({
            "header": header_text,
            "details": details_text,
            "image_url": img_url
        })

    # Download images for each channel (if available)
    async with aiohttp.ClientSession() as session:
        for channel in channels:
            if channel.get("image_url"):
                img = await fetch_image(session, channel["image_url"])
                channel["image"] = img

    # Create a new base image for the display.
    base_image = Image.new("RGB", TARGET_RESOLUTION, "white")
    # Allocate vertical blocks for each channel (assume two channels).
    channel_height = TARGET_RESOLUTION[1] // 2
    block1 = (0, 0, TARGET_RESOLUTION[0], channel_height)
    block2 = (0, channel_height, TARGET_RESOLUTION[0], TARGET_RESOLUTION[1] - channel_height)
    if len(channels) > 0:
        render_channel_block(base_image, channels[0], block1)
    if len(channels) > 1:
        render_channel_block(base_image, channels[1], block2)

    # Final processing: rotate if needed, then ensure the image fits exactly the target resolution.
    if base_image.height > base_image.width:
        base_image = base_image.rotate(90, expand=True)
    base_image = ImageOps.contain(base_image, TARGET_RESOLUTION)
    if base_image.size != TARGET_RESOLUTION:
        base_image = fill_letterbox(base_image, *TARGET_RESOLUTION)
    output_buffer = io.BytesIO()
    base_image.save(output_buffer, format="BMP")
    return Response(content=output_buffer.getvalue(), media_type="image/bmp")