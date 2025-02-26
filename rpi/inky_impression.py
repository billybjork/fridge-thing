#!/usr/bin/env python3
import os
import sys
import time
import logging
import requests
from datetime import datetime
from pathlib import Path
from PIL import Image

# Instead of importing InkyImpression directly, we use auto() to detect the display.
from inky.auto import auto

# -------------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------------

def get_device_uuid() -> str:
    """
    Retrieves the MAC address from wlan0 and returns a formatted string as the device UUID.
    """
    try:
        with open("/sys/class/net/wlan0/address", "r") as f:
            mac = f.read().strip()  # e.g., "b8:27:eb:12:34:56"
        # Remove colons and convert to uppercase to form a simple unique ID.
        uuid = mac.upper().replace(":", "")
        return uuid
    except Exception as e:
        log_event(f"Failed to get MAC address: {e}")
        return "DEFAULT-UUID"

API_BASE_URL = os.getenv("API_BASE_URL", "https://fridge-thing-production.up.railway.app")
# Use the MAC address as the device UUID.
DEVICE_UUID = get_device_uuid()

# When the API returns "NO_REFRESH", it means no image update during a no‑refresh period.
NO_REFRESH_MARKER = "NO_REFRESH"

# Where to save the downloaded image and log file.
LOCAL_IMAGE_PATH = "/tmp/inky_display.bmp"
LOG_FILE_PATH = "/home/pi/inky_display.log"  # adjust as needed for your SD card mount

# Timeout settings (in seconds)
HTTP_TIMEOUT = 10
ERROR_RETRY_DELAY = 60  # wait 60 sec before retrying on errors

# -------------------------------------------------------------------------------
# Logging Setup
# -------------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE_PATH),
        logging.StreamHandler(sys.stdout)
    ]
)

def log_event(message: str):
    logging.info(message)

# -------------------------------------------------------------------------------
# Display Helpers
# -------------------------------------------------------------------------------

def display_message(inky, message: str):
    """
    Clears the display and shows a text message. Useful for errors or status updates.
    """
    from PIL import ImageDraw, ImageFont

    # Create a blank image with the same dimensions as the display.
    img = Image.new("P", (inky.WIDTH, inky.HEIGHT), color=inky.WHITE)
    draw = ImageDraw.Draw(img)
    
    # Use a custom font if available:
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf", 22)
    except Exception:
        font = None

    # Draw the message near the top-left of the display.
    draw.text((10, 10), message, fill=inky.BLACK, font=font)
    
    inky.set_image(img)
    inky.show()
    log_event(f"Display updated with message: '{message}'")

# -------------------------------------------------------------------------------
# API & Image Download Functions
# -------------------------------------------------------------------------------

def ping_api() -> dict:
    """
    Calls the device display API and returns a dict with keys:
      - image_url (str)
      - next_wake_secs (int)
    """
    endpoint = f"{API_BASE_URL}/api/devices/{DEVICE_UUID}/display"
    log_event(f"Pinging API: {endpoint}")
    try:
        response = requests.post(endpoint, timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            log_event(f"API returned non-200 status: {response.status_code}")
            return {}
        data = response.json()
        log_event(f"API response: {data}")
        return data
    except Exception as e:
        log_event(f"Exception during API ping: {e}")
        return {}

def download_image(url: str, local_path: str) -> bool:
    """
    Downloads the image from the provided URL to the local_path.
    Returns True on success, False on error.
    """
    log_event(f"Downloading image from {url}")
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            log_event(f"Failed to download image; HTTP status: {resp.status_code}")
            return False
        with open(local_path, 'wb') as f:
            f.write(resp.content)
        log_event(f"Image successfully downloaded to {local_path}")
        return True
    except Exception as e:
        log_event(f"Exception during image download: {e}")
        return False

# -------------------------------------------------------------------------------
# Main Logic
# -------------------------------------------------------------------------------

def main():
    # Initialize the Inky display using auto-detection.
    try:
        inky = auto()  # auto() detects the correct display type.
        inky.set_border(inky.WHITE)
        log_event("Display initialized successfully.")
    except Exception as e:
        log_event(f"Display initialization error: {e}")
        sys.exit(1)
    
    # Display an initializing message.
    display_message(inky, "Initializing...")

    # Ping the API for the next image and wakeup interval.
    api_data = ping_api()
    if not api_data:
        display_message(inky, "API Error")
        time.sleep(ERROR_RETRY_DELAY)
        return

    image_url = api_data.get("image_url", "")
    next_wake_secs = int(api_data.get("next_wake_secs", 3600))

    if image_url == NO_REFRESH_MARKER:
        log_event("Received NO_REFRESH marker; skipping image update.")
        display_message(inky, "No Refresh")
        time.sleep(next_wake_secs)
        return

    # Download the image.
    if not download_image(image_url, LOCAL_IMAGE_PATH):
        display_message(inky, "Download Fail")
        time.sleep(ERROR_RETRY_DELAY)
        return

    # Render the image.
    try:
        img = Image.open(LOCAL_IMAGE_PATH)
        inky.set_image(img)
        inky.show()
        log_event("Image rendered to display.")
    except Exception as e:
        log_event(f"Error rendering image: {e}")
        display_message(inky, "Render Error")
        time.sleep(ERROR_RETRY_DELAY)
        return

    log_event(f"Sleeping for {next_wake_secs} seconds before next update.")
    time.sleep(next_wake_secs)

# -------------------------------------------------------------------------------
# Main Loop
# -------------------------------------------------------------------------------

if __name__ == '__main__':
    while True:
        main()