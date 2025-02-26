#!/usr/bin/env python3
import os
import sys
import time
import logging
import requests
import hashlib
import json
import subprocess
from datetime import datetime, timedelta
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
DATA_DIR = "/home/pi/inky_data"
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# Configuration file for storing state between runs
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

API_BASE_URL = os.getenv("API_BASE_URL", "https://fridge-thing-production.up.railway.app")
# Use the MAC address as the device UUID.
DEVICE_UUID = get_device_uuid()

# When the API returns "NO_REFRESH", it means no image update during a no‑refresh period.
NO_REFRESH_MARKER = "NO_REFRESH"

# Where to save the downloaded image and log files
LOCAL_IMAGE_PATH = os.path.join(DATA_DIR, "inky_display.bmp")
LAST_IMAGE_PATH = os.path.join(DATA_DIR, "last_display.bmp")
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
STATE_POWER_SAVING = "POWER_SAVING"

# Power saving settings
NIGHT_MODE_START = 23  # 11 PM
NIGHT_MODE_END = 6     # 6 AM
NIGHT_MODE_INTERVAL = 3 * 3600  # 3 hours in seconds
MIN_BATTERY_PCT = 15   # Percentage below which to enter power saving mode

# -------------------------------------------------------------------------------
# System Power Management
# -------------------------------------------------------------------------------

def enable_power_savings():
    """Enable various system-level power saving features"""
    try:
        # Enable WiFi power management
        os.system("sudo iwconfig wlan0 power on")
        
        # Set CPU to powersave governor
        os.system("echo powersave | sudo tee /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor > /dev/null")
        
        # Disable Bluetooth if available
        os.system("sudo systemctl stop bluetooth.service")
        os.system("sudo rfkill block bluetooth")
        
        log_event("Power saving features enabled")
    except Exception as e:
        log_event(f"Error enabling power savings: {e}")

def get_battery_percentage():
    """
    Get battery percentage if available.
    This assumes you have a UPS/battery HAT with i2c interface.
    Modify according to your specific hardware.
    """
    try:
        # This is an example for a typical battery monitoring HAT
        # Adjust according to your specific hardware
        result = subprocess.run(['i2cget', '-y', '1', '0x36', '0x04'], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            value = int(result.stdout.strip(), 16)
            percentage = min(100, max(0, value))
            return percentage
    except Exception as e:
        log_event(f"Battery monitoring error: {e}")
    
    # Return default if not available
    return 100

def schedule_next_wakeup(seconds):
    """
    Schedule the next wakeup using systemd or RTC if available.
    This function schedules a wakeup and then shuts down the Pi.
    """
    wakeup_time = datetime.now() + timedelta(seconds=seconds)
    log_event(f"Scheduling next wakeup at {wakeup_time}")
    
    # Save when we should next wake up
    save_config({"next_wakeup": wakeup_time.timestamp(), 
                "wakeup_seconds": seconds})
    
    try:
        # Method 1: Using RTC (if you have a hardware RTC)
        # Uncomment and adjust for your specific RTC hardware
        # rtc_time = wakeup_time.strftime("%y %m %d %H %M %S")
        # os.system(f"sudo hwclock --set --date='{rtc_time}'")
        # os.system("sudo shutdown -h now")
        
        # Method 2: Using systemd timer (more compatible)
        # Create systemd wakeup timer
        timer_time = seconds
        os.system(f"sudo shutdown -h +{timer_time//60}")
        
        log_state(STATE_POWER_SAVING, f"Shutting down for {seconds} seconds")
    except Exception as e:
        log_event(f"Error scheduling shutdown: {e}")

def is_night_time():
    """Check if current time is within night time hours"""
    current_hour = datetime.now().hour
    if NIGHT_MODE_START <= current_hour or current_hour < NIGHT_MODE_END:
        return True
    return False

# -------------------------------------------------------------------------------
# Configuration Management
# -------------------------------------------------------------------------------

def load_config():
    """Load configuration from file"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        log_event(f"Error loading config: {e}")
    return {}

def save_config(config):
    """Save configuration to file"""
    try:
        # Merge with existing config
        existing = load_config()
        existing.update(config)
        
        with open(CONFIG_FILE, 'w') as f:
            json.dump(existing, f)
    except Exception as e:
        log_event(f"Error saving config: {e}")

def calculate_image_hash(image_path):
    """Calculate a hash of the image file to detect changes"""
    try:
        if not os.path.exists(image_path):
            return None
            
        with open(image_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception as e:
        log_event(f"Error calculating image hash: {e}")
        return None

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
    
    # Add power info if available
    battery_pct = get_battery_percentage()
    if battery_pct < 100:
        draw.text((10, inky.HEIGHT - 30), f"Battery: {battery_pct}%", fill=inky.BLACK, font=font)
    
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
    
    # Get battery percentage
    battery_pct = get_battery_percentage()
    
    try:
        # Include battery info in the API request
        payload = {
            "battery_pct": battery_pct
        }
        
        response = requests.post(endpoint, json=payload, timeout=HTTP_TIMEOUT)
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
    # Enable power saving features
    enable_power_savings()
    
    # Load configuration
    config = load_config()
    
    # Check battery level
    battery_pct = get_battery_percentage()
    log_event(f"Battery level: {battery_pct}%")
    
    # Enter power saving mode if battery is low
    if battery_pct < MIN_BATTERY_PCT:
        log_state(STATE_POWER_SAVING, f"Low battery ({battery_pct}%)")
        # Wait longer when battery is low
        schedule_next_wakeup(3 * 3600)  # 3 hours
        sys.exit(0)
    
    # Initialize the Inky display using auto-detection
    try:
        inky = auto()  # auto() detects the correct display type.
        inky.set_border(inky.WHITE)
        log_event("Display initialized successfully.")
    except Exception as e:
        log_event(f"Display initialization error: {e}")
        sys.exit(1)
    
    # Display an initializing message only on first run
    if not os.path.exists(CONFIG_FILE):
        display_message(inky, "Initializing...", STATE_INITIALIZING)
    else:
        # For subsequent runs, just log without display update
        log_event("Starting update cycle")

    # Ping the API for the next image and wakeup interval
    api_data = ping_api()
    if not api_data:
        display_message(inky, "API Error", STATE_API_ERROR)
        # Schedule a retry with shorter interval
        schedule_next_wakeup(ERROR_RETRY_DELAY)
        sys.exit(0)

    image_url = api_data.get("image_url", "")
    next_wake_secs = int(api_data.get("next_wake_secs", 3600))

    # Check if we should use night mode schedule
    if is_night_time():
        log_event("Night mode active, using longer refresh interval")
        next_wake_secs = max(next_wake_secs, NIGHT_MODE_INTERVAL)

    if image_url == NO_REFRESH_MARKER:
        log_event("Received NO_REFRESH marker; skipping image update.")
        # Just log this state, don't update display
        log_state(STATE_NO_REFRESH, f"Next update in {next_wake_secs}s")
        schedule_next_wakeup(next_wake_secs)
        sys.exit(0)

    # Download the image
    if not download_image(image_url, LOCAL_IMAGE_PATH):
        display_message(inky, "Download Fail", STATE_DOWNLOAD_ERROR)
        schedule_next_wakeup(ERROR_RETRY_DELAY)
        sys.exit(0)

    # Compare with previous image to see if it's changed
    current_hash = calculate_image_hash(LOCAL_IMAGE_PATH)
    previous_hash = config.get('last_image_hash')
    
    # If image is the same as before, we can potentially skip
    if current_hash and previous_hash and current_hash == previous_hash:
        log_event("Image unchanged from previous display")
        # Still render if it's been a long time since last refresh
        last_refresh_time = config.get('last_refresh_time', 0)
        time_since_refresh = time.time() - last_refresh_time
        
        if time_since_refresh < 24 * 3600:  # If less than 24 hours, skip
            log_event("Skipping refresh as image is unchanged")
            # Save the hash
            save_config({'last_image_hash': current_hash})
            schedule_next_wakeup(next_wake_secs)
            sys.exit(0)
    
    # Render the image
    try:
        img = Image.open(LOCAL_IMAGE_PATH)
        inky.set_image(img)
        inky.show()
        log_state(STATE_DISPLAYING_IMAGE, f"Image from {image_url}")
        
        # Save image hash and refresh time
        save_config({
            'last_image_hash': current_hash,
            'last_refresh_time': time.time()
        })
        
        # Copy to last image location
        import shutil
        shutil.copy(LOCAL_IMAGE_PATH, LAST_IMAGE_PATH)
        
    except Exception as e:
        log_event(f"Error rendering image: {e}")
        display_message(inky, "Render Error", STATE_RENDER_ERROR)
        schedule_next_wakeup(ERROR_RETRY_DELAY)
        sys.exit(0)

    log_event(f"Scheduling next wake in {next_wake_secs} seconds")
    schedule_next_wakeup(next_wake_secs)
    sys.exit(0)

# -------------------------------------------------------------------------------
# Main Entry Point
# -------------------------------------------------------------------------------

if __name__ == '__main__':
    # Log startup with device info
    device_info = f"Device UUID: {DEVICE_UUID}"
    log_event("===== INKY DISPLAY SERVICE STARTING =====")
    log_event(device_info)
    log_state("SERVICE_START", device_info)
    
    try:
        main()
    except KeyboardInterrupt:
        log_event("Service stopped by user")
        log_state("SERVICE_STOP", "User interrupt")
        sys.exit(0)
    except Exception as e:
        log_event(f"Unhandled exception: {e}")
        log_state("SERVICE_CRASH", str(e))
        sys.exit(1)