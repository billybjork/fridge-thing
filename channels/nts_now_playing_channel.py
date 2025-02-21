import os
import io
import aiohttp
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
        # Try loading a truetype font from system (or provide a path to your font)
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        # Fallback to default PIL font if not available
        return ImageFont.load_default()

def render_channels_image(channels):
    """
    Create a new image (600x448) and draw the channel information.
    channels is a list of dictionaries containing channel info.
    """
    # Create a blank white image
    image = Image.new("RGB", TARGET_RESOLUTION, "white")
    draw = ImageDraw.Draw(image)

    # Define some margins and spacing
    margin = 10
    y = margin

    # Title
    title_font = get_default_font(24)
    draw.text((margin, y), "NTS Live - Now Playing", fill="black", font=title_font)
    y += 30

    # For each channel, draw its details
    for ch in channels:
        # Draw a separator line between channels
        if y > margin + 30:
            draw.line((margin, y, TARGET_RESOLUTION[0]-margin, y), fill="grey", width=1)
            y += 5

        # Channel number and location (bold)
        header_font = get_default_font(20)
        header_text = f"Channel {ch.get('number', '?')} - {ch.get('location', '')}"
        draw.text((margin, y), header_text, fill="black", font=header_font)
        y += 25

        # Broadcast times
        times_font = get_default_font(16)
        times_text = f"Times: {ch.get('times', 'N/A')}"
        draw.text((margin, y), times_text, fill="black", font=times_font)
        y += 20

        # Program title
        prog_font = get_default_font(18)
        prog_text = f"Show: {ch.get('title', 'N/A')}"
        draw.text((margin, y), prog_text, fill="black", font=prog_font)
        y += 25

        # Broadcast description (wrap if too long)
        desc_font = get_default_font(14)
        desc_text = ch.get("description", "")
        # Simple text wrap: split into lines of at most 50 characters
        lines = []
        words = desc_text.split()
        line = ""
        for word in words:
            if len(line) + len(word) + 1 <= 50:
                line += (" " if line else "") + word
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        for l in lines:
            draw.text((margin, y), l, fill="black", font=desc_font)
            y += 18

        y += 10  # space between channels

        # Stop if running out of space
        if y > TARGET_RESOLUTION[1] - 40:
            break

    return image

@router.get("/api/nts_now_playing_convert", name="convert_nts_now_playing")
async def convert_nts_now_playing(request: Request, device_uuid: str = "0"):
    """
    Fetch the NTS.live homepage, scrape the specific channels section, reconstruct a custom
    image with the key channel details tailored for the 600x448 inkplate display.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PAGE_URL) as resp:
                if resp.status != 200:
                    raise Exception("Page fetch error")
                html_content = await resp.text()
    except Exception as e:
        # On exception, fallback to the default fallback image.
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                image_bytes = await fallback_resp.read()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        # Standard processing below:
        if image.height > image.width:
            image = image.rotate(90, expand=True)
        image = ImageOps.contain(image, TARGET_RESOLUTION)
        if image.size != TARGET_RESOLUTION:
            image = fill_letterbox(image, *TARGET_RESOLUTION)
        output_buffer = io.BytesIO()
        image.save(output_buffer, format="BMP")
        return Response(content=output_buffer.getvalue(), media_type="image/bmp")

    # Parse HTML with BeautifulSoup
    soup = BeautifulSoup(html_content, "html.parser")
    # Use the provided CSS selector
    container = soup.select_one("#nts-live-header > div.live-header__channels--expanded.live-header__channels")
    
    channels_data = []
    if container:
        # Find all direct channel entries (div with class "live-channel")
        channel_divs = container.find_all("div", class_="live-channel")
        for div in channel_divs:
            ch_info = {}
            # Channel number: look for span with class "channel-icon"
            num_tag = div.find("span", class_="channel-icon")
            ch_info["number"] = num_tag.get_text(strip=True) if num_tag else "?"
            # Broadcast location: span with class "broadcast-location"
            loc_tag = div.find("span", class_="broadcast-location")
            ch_info["location"] = loc_tag.get_text(strip=True) if loc_tag else ""
            # Broadcast times: element with class "live-channel__header__broadcast-times"
            times_tag = div.find("span", class_="live-channel__header__broadcast-times")
            ch_info["times"] = times_tag.get_text(strip=True) if times_tag else "N/A"
            # Show title: h3 with class "broadcast-heading"
            title_tag = div.find("h3", class_="broadcast-heading")
            ch_info["title"] = title_tag.get_text(strip=True) if title_tag else "N/A"
            # Description: p with class "broadcast-description"
            desc_tag = div.find("p", class_="broadcast-description")
            ch_info["description"] = desc_tag.get_text(strip=True) if desc_tag else ""
            channels_data.append(ch_info)
    else:
        # If selector not found, fallback to default image
        async with aiohttp.ClientSession() as session:
            async with session.get(DEFAULT_FALLBACK_IMAGE) as fallback_resp:
                image_bytes = await fallback_resp.read()
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if image.height > image.width:
            image = image.rotate(90, expand=True)
        image = ImageOps.contain(image, TARGET_RESOLUTION)
        if image.size != TARGET_RESOLUTION:
            image = fill_letterbox(image, *TARGET_RESOLUTION)
        output_buffer = io.BytesIO()
        image.save(output_buffer, format="BMP")
        return Response(content=output_buffer.getvalue(), media_type="image/bmp")
    
    # Build a new image based on the scraped channel info.
    custom_image = render_channels_image(channels_data)
    
    # Standard processing: rotate if needed, resize/letterbox if not exactly TARGET_RESOLUTION.
    if custom_image.height > custom_image.width:
        custom_image = custom_image.rotate(90, expand=True)
    custom_image = ImageOps.contain(custom_image, TARGET_RESOLUTION)
    if custom_image.size != TARGET_RESOLUTION:
        custom_image = fill_letterbox(custom_image, *TARGET_RESOLUTION)
    
    # Save processed image to BMP and return.
    output_buffer = io.BytesIO()
    custom_image.save(output_buffer, format="BMP")
    return Response(content=output_buffer.getvalue(), media_type="image/bmp")