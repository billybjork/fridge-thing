#!/usr/bin/env python3
import os
import sys
import time
import logging
import requests
from datetime import datetime
from pathlib import Path
from PIL import Image
from logging.handlers import RotatingFileHandler

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

# Create logs directory if it doesn't exist
LOGS_DIR = "/home/pi/inky_logs"
os.makedirs(LOGS_DIR, exist_ok=True)

API_BASE_URL = os.getenv("API_BASE_URL", "https://fridge-thing-production.up.railway.app")
# Use the MAC address as the device UUID.
DEVICE_UUID = get_device_uuid()

# When the API returns "NO_REFRESH", it means no image update during a no‑refresh period.
NO_REFRESH_MARKER = "NO_REFRESH"

# Where to save the downloaded image and log file.
LOCAL_IMAGE_PATH = "/tmp/inky_display.bmp"
LOG_FILE_PATH = os.path.join(LOGS_DIR, "inky_display.log")
STATE_LOG_PATH = os.path.join(LOGS_DIR, "inky_states.log")

# Timeout settings (in seconds)
HTTP_TIMEOUT = 10
ERROR_RETRY_DELAY = 60  # wait 60 sec before retrying on errors

# State constants
STATE_INITIALIZING = "INITIALIZING"
STATE_API_ERROR = "API_ERROR"
STATE_DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
STATE_RENDER_ERROR = "RENDER_ERROR"
STATE_NO_REFRESH = "NO_REFRESH"
STATE_DISPLAYING_IMAGE = "DISPLAYING_IMAGE"

# -------------------------------------------------------------------------------
# Logging Setup
# -------------------------------------------------------------------------------

# Configure main logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE_PATH, maxBytes=1024*1024, backupCount=5),  # 1MB max size, keep 5 backups
        logging.StreamHandler(sys.stdout)
    ]
)

# Create a separate logger for state changes
state_logger = logging.getLogger("state_logger")
state_logger.setLevel(logging.INFO)
state_handler = RotatingFileHandler(STATE_LOG_PATH, maxBytes=1024*1024, backupCount=3)
state_formatter = logging.Formatter('%(asctime)s - %(message)s')
state_handler.setFormatter(state_formatter)
state_logger.addHandler(state_handler)
state_logger.propagate = False  # Don't send to root logger

def log_event(message: str):
    """Log general events to the main log"""
    logging.info(message)

def log_state(state: str, message: str = ""):
    """Log state changes to the dedicated state log file"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state_info = f"{timestamp} - STATE: {state}"
    if message:
        state_info += f" - {message}"
    
    # Write directly to the state log file to ensure it's always captured
    try:
        with open(STATE_LOG_PATH, 'a') as f:
            f.write(state_info + "\n")
    except Exception as e:
        logging.error(f"Failed to write to state log: {e}")
    
    # Also log to main logger
    logging.info(f"STATE CHANGE: {state} - {message}")

# -------------------------------------------------------------------------------
# Display Helpers
# -------------------------------------------------------------------------------

def should_update_display(state: str) -> bool:
    """
    Determines if the display should be refreshed based on the current state.
    Only updates for error states and initialization, not for transitional states.
    """
    # Only update display for error states or the very first initialization
    return state in [STATE_API_ERROR, STATE_DOWNLOAD_ERROR, STATE_RENDER_ERROR, STATE_INITIALIZING]

def display_message(inky, message: str, state: str):
    """
    Conditionally updates the display with a message based on the current state.
    """
    # Log the state change regardless of whether we update the display
    log_state(state, message)
    
    # Only update the display if this is a critical state
    if not should_update_display(state):
        log_event("Display update skipped for non-critical state")
        return
        
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
    
    # Display an initializing message only on first run
    if not hasattr(main, 'initialized'):
        display_message(inky, "Initializing...", STATE_INITIALIZING)
        main.initialized = True
    else:
        # For subsequent runs, just log without display update
        log_event("Starting update cycle")

    # Ping the API for the next image and wakeup interval.
    api_data = ping_api()
    if not api_data:
        display_message(inky, "API Error", STATE_API_ERROR)
        time.sleep(ERROR_RETRY_DELAY)
        return

    image_url = api_data.get("image_url", "")
    next_wake_secs = int(api_data.get("next_wake_secs", 3600))

    if image_url == NO_REFRESH_MARKER:
        log_event("Received NO_REFRESH marker; skipping image update.")
        # Just log this state, don't update display
        log_state(STATE_NO_REFRESH, f"Next update in {next_wake_secs}s")
        time.sleep(next_wake_secs)
        return

    # Download the image.
    if not download_image(image_url, LOCAL_IMAGE_PATH):
        display_message(inky, "Download Fail", STATE_DOWNLOAD_ERROR)
        time.sleep(ERROR_RETRY_DELAY)
        return

    # Render the image.
    try:
        img = Image.open(LOCAL_IMAGE_PATH)
        inky.set_image(img)
        inky.show()
        log_state(STATE_DISPLAYING_IMAGE, f"Image from {image_url}")
    except Exception as e:
        log_event(f"Error rendering image: {e}")
        display_message(inky, "Render Error", STATE_RENDER_ERROR)
        time.sleep(ERROR_RETRY_DELAY)
        return

    log_event(f"Sleeping for {next_wake_secs} seconds before next update.")
    time.sleep(next_wake_secs)

# -------------------------------------------------------------------------------
# Main Loop
# -------------------------------------------------------------------------------

if __name__ == '__main__':
    # Log startup with device info
    device_info = f"Device UUID: {DEVICE_UUID}"
    log_event("===== INKY DISPLAY SERVICE STARTING =====")
    log_event(device_info)
    log_state("SERVICE_START", device_info)
    
    try:
        while True:
            main()
    except KeyboardInterrupt:
        log_event("Service stopped by user")
        log_state("SERVICE_STOP", "User interrupt")
        sys.exit(0)
    except Exception as e:
        log_event(f"Unhandled exception: {e}")
        log_state("SERVICE_CRASH", str(e))
        sys.exit(1)