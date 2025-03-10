#!/usr/bin/env python3
import os
import sys
import time
import logging
import requests
import subprocess
# import hashlib - removed image comparison functionality
import json
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from logging.handlers import RotatingFileHandler

# Instead of importing InkyImpression directly, we use auto() to detect the display.
from inky.auto import auto

# -------------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------------

# Base directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Check multiple SD card locations
SD_CARD_PATHS = ["/mnt/sd", "/media/pi/SD", "/home/pi/sd"]
# Find first existing SD card path or use a fallback
SD_CARD_PATH = next((path for path in SD_CARD_PATHS if os.path.exists(path)), "/home/pi")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# API Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "https://fridge-thing-production.up.railway.app")
DEVICE_UUID = None  # Will be set during startup

# File paths
LOCAL_IMAGE_PATH = "/tmp/inky_display.bmp"
LOG_FILE_PATH = os.path.join(LOGS_DIR, "inky_display.log")
STATE_LOG_PATH = os.path.join(LOGS_DIR, "inky_states.log")
WIFI_CREDENTIALS_PATH = os.path.join(SD_CARD_PATH, "wifi.txt")

# Marker for no refresh response from server
NO_REFRESH_MARKER = "NO_REFRESH"

# Timeout settings (in seconds)
HTTP_TIMEOUT = 10
BASE_ERROR_RETRY_DELAY = 60  # base delay for retries
MAX_ERROR_RETRY_DELAY = 3600  # max 1 hour delay

# State constants
STATE_INITIALIZING = "INITIALIZING"
STATE_CONNECTING_WIFI = "CONNECTING_WIFI"
STATE_WIFI_ERROR = "WIFI_ERROR"
STATE_API_ERROR = "API_ERROR"
STATE_DOWNLOAD_ERROR = "DOWNLOAD_ERROR"
STATE_RENDER_ERROR = "RENDER_ERROR"
STATE_NO_REFRESH = "NO_REFRESH"
STATE_DISPLAYING_IMAGE = "DISPLAYING_IMAGE"
STATE_NO_CHANGE = "NO_CHANGE"
STATE_SLEEPING = "SLEEPING"

# Firmware/Version information
CURRENT_VERSION = "1.0"

# Log batching settings
MAX_LOG_BATCH_SIZE = 10
log_batch = []

# Current state tracking
current_state = STATE_INITIALIZING
error_code = 0
retry_attempt = 0

# RTC Time Sync
rtc_last_sync = 0
RTC_SYNC_INTERVAL = 86400  # 24 hours in seconds

# -------------------------------------------------------------------------------
# Logging Setup
# -------------------------------------------------------------------------------

# Configure main logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE_PATH, maxBytes=1024*1024, backupCount=5),
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

def format_timestamp(dt=None):
    """Format timestamp for logging in the same format as ESP32"""
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def log_event(message):
    """Log general events to the main log with batching to reduce I/O"""
    global log_batch
    log_batch.append(message)
    
    # Only write to log when batch is full or on important messages
    if len(log_batch) >= MAX_LOG_BATCH_SIZE or "ERROR" in message or "STATE" in message:
        flush_log_batch()

def flush_log_batch():
    """Write accumulated log messages to the log file"""
    global log_batch
    if log_batch:
        for msg in log_batch:
            logging.info(msg)
        log_batch = []

def log_state(state, message=""):
    """Log state changes to the dedicated state log file"""
    global current_state
    
    # Only log state if it's changed
    if state != current_state or "ERROR" in state:
        current_state = state
        
        timestamp = format_timestamp()
        state_info = f"{timestamp}: State changed to: {state}"
        if message:
            state_info += f" - {message}"
        
        # Write to state log file
        try:
            with open(STATE_LOG_PATH, 'a') as f:
                f.write(state_info + "\n")
        except Exception as e:
            logging.error(f"Failed to write to state log: {e}")
        
        # Also log to main logger
        log_event(state_info)
        flush_log_batch()  # Ensure state changes are logged immediately

def set_state(new_state, error_code=0, message=""):
    """Set the current state and log it, similar to the ESP32 setState function"""
    global current_state
    
    if message:
        log_state(new_state, message)
    else:
        log_state(new_state)
        
    current_state = new_state
    return current_state

# -------------------------------------------------------------------------------
# WiFi Management Functions
# -------------------------------------------------------------------------------

def get_device_uuid():
    """Get a unique device identifier based on the Raspberry Pi's MAC address"""
    try:
        with open("/sys/class/net/wlan0/address", "r") as f:
            mac = f.read().strip()
        # Remove colons and convert to uppercase (like ESP32 implementation)
        uuid = mac.replace(":", "").upper()
        log_event(f"Device UUID: {uuid}")
        return uuid
    except Exception as e:
        log_event(f"Failed to get MAC address: {e}")
        # Fallback to a default UUID
        return "RASPI-DEFAULT-UUID"

def read_wifi_credentials_from_sd():
    """
    Read WiFi credentials from wifi.txt file on the SD card.
    Format should be:
    NETWORK=YourNetworkName
    PASSWORD=YourPassword
    
    Returns tuple of (ssid, password) or (None, None) if not found
    """
    if not os.path.exists(WIFI_CREDENTIALS_PATH):
        log_event(f"WiFi credentials file not found at {WIFI_CREDENTIALS_PATH}")
        return None, None
    
    try:
        with open(WIFI_CREDENTIALS_PATH, 'r') as f:
            content = f.read()
            
        ssid = None
        password = None
        
        # Parse NETWORK=value
        network_match = content.find("NETWORK=")
        if network_match >= 0:
            network_end = content.find('\n', network_match)
            if network_end < 0:
                network_end = len(content)
            ssid = content[network_match + 8:network_end].strip()
        
        # Parse PASSWORD=value
        password_match = content.find("PASSWORD=")
        if password_match >= 0:
            password_end = content.find('\n', password_match)
            if password_end < 0:
                password_end = len(content)
            password = content[password_match + 9:password_end].strip()
        
        if ssid and password:
            log_event(f"Successfully read WiFi credentials from SD card for network: {ssid}")
            return ssid, password
        else:
            log_event("ERROR: Invalid WiFi credentials format in wifi.txt")
            return None, None
            
    except Exception as e:
        log_event(f"ERROR: Failed to read WiFi credentials: {e}")
        return None, None

def check_wifi_connection():
    """Check if WiFi is connected and try to connect if not"""
    try:
        # Check if connected by trying to reach a reliable host
        process = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE
        )
        
        if process.returncode == 0:
            log_event("WiFi is connected")
            return True
        else:
            log_event("WiFi is not connected, attempting to reconnect")
            # Try to reconnect using system commands
            try_connect_wifi()
            
            # Check again after connection attempt
            process = subprocess.run(
                ["ping", "-c", "1", "-W", "5", "8.8.8.8"],
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            
            if process.returncode == 0:
                log_event("WiFi reconnected successfully")
                return True
            else:
                log_event("WiFi reconnection failed")
                set_state(STATE_WIFI_ERROR, message="Could not connect to WiFi")
                return False
                
    except Exception as e:
        log_event(f"ERROR: WiFi connection check failed: {e}")
        set_state(STATE_WIFI_ERROR, message=str(e))
        return False

def try_connect_wifi():
    """Attempt to reconnect WiFi using credential files and system commands"""
    # First try using the wifi.txt file from SD card
    ssid, password = read_wifi_credentials_from_sd()
    
    if ssid and password:
        try:
            # This approach uses wpa_cli which should be available on Raspberry Pi
            log_event(f"Attempting to connect to WiFi network: {ssid}")
            
            # Generate a wpa_supplicant network configuration
            network_config = (
                f'network={{\n'
                f'    ssid="{ssid}"\n'
                f'    psk="{password}"\n'
                f'    key_mgmt=WPA-PSK\n'
                f'}}\n'
            )
            
            # Write to a temporary file
            with open('/tmp/wifi_network.conf', 'w') as f:
                f.write(network_config)
            
            # Reconfigure wpa_supplicant
            subprocess.run(['wpa_cli', '-i', 'wlan0', 'reconfigure'], 
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Give it time to connect
            time.sleep(10)
            
            return True
        except Exception as e:
            log_event(f"ERROR: Failed to configure WiFi: {e}")
            return False
    
    # If we got here, we couldn't connect
    log_event("ERROR: No valid WiFi credentials available")
    return False

# -------------------------------------------------------------------------------
# Power Management Functions
# -------------------------------------------------------------------------------

def enable_power_savings():
    """Enable various power-saving features on the Pi"""
    log_event("Enabling power saving features")
    
    try:
        # Disable Bluetooth if not needed
        subprocess.run(["sudo", "rfkill", "block", "bluetooth"], check=False)
        
        # Try to adjust CPU governor for power savings
        try:
            subprocess.run(["sudo", "cpufreq-set", "-g", "powersave"], check=False)
        except:
            pass  # Ignore if cpufreq-set is not available
        
        log_event("Power saving features enabled")
        
    except Exception as e:
        log_event(f"Error enabling power savings: {e}")

def prepare_for_sleep(sleep_seconds):
    """
    Prepare system for sleep and create necessary files for systemd timer
    to wake up the system at the appropriate time.
    """
    log_event(f"Preparing for sleep for {sleep_seconds} seconds")
    
    # Calculate wake time
    wake_timestamp = int(time.time()) + sleep_seconds
    wake_datetime = datetime.fromtimestamp(wake_timestamp)
    
    # Format for systemd timer
    formatted_wake = wake_datetime.strftime("%Y-%m-%d %H:%M:%S")
    
    # Save wake time to a file that can be read by systemd
    try:
        with open('/tmp/inky_next_wake.txt', 'w') as f:
            f.write(str(wake_timestamp))
        log_event(f"Next wake time set to: {formatted_wake}")
    except Exception as e:
        log_event(f"ERROR: Failed to save wake time: {e}")
    
    # Ensure logs are written before exiting
    flush_log_batch()

def power_off_display(inky):
    """Turn off power to the display when not in use"""
    try:
        # If display supports power down mode
        if hasattr(inky, "sleep"):
            inky.sleep()
            log_event("Display put to sleep")
    except Exception as e:
        log_event(f"Error putting display to sleep: {e}")

# -------------------------------------------------------------------------------
# Display Helpers
# -------------------------------------------------------------------------------

def should_update_display(state):
    """
    Determines if the display should be refreshed based on current state.
    Only updates for critical error states and new images.
    """
    # Only update for critical states or displaying new images
    return state in [
        STATE_WIFI_ERROR, 
        STATE_API_ERROR, 
        STATE_DOWNLOAD_ERROR, 
        STATE_RENDER_ERROR,
        STATE_DISPLAYING_IMAGE
    ]

def display_message(inky, message, state):
    """
    Updates the display with a message based on the current state.
    Only refreshes display for critical states.
    """
    # Set the state first
    set_state(state, message=message)
    
    # Skip display updates for non-critical states
    if not should_update_display(state):
        log_event("Display update skipped for non-critical state")
        return
    
    try:
        # Create a blank image with the same dimensions as the display
        img = Image.new("P", (inky.WIDTH, inky.HEIGHT), color=inky.WHITE)
        draw = ImageDraw.Draw(img)
        
        # Use a custom font if available
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf", 22)
        except Exception:
            font = None
        
        # Draw the message near the top-left of the display
        draw.text((10, 10), message, fill=inky.BLACK, font=font)
        
        # Add timestamp and device ID at bottom
        footer = f"{format_timestamp()} - {DEVICE_UUID}"
        draw.text((10, inky.HEIGHT - 30), footer, fill=inky.BLACK, font=font)
        
        # Update display with a full refresh
        inky.set_image(img)
        inky.show()
        log_event(f"Display updated with message: '{message}'")
    except Exception as e:
        log_event(f"ERROR: Failed to update display: {e}")

# -------------------------------------------------------------------------------
# API & Image Download Functions
# -------------------------------------------------------------------------------

def ping_api():
    """
    Calls the device display API and returns a dict with:
      - image_url (str)
      - next_wake_secs (int)
      - time (dict) - optional RTC time info
    """
    endpoint = f"{API_BASE_URL}/api/devices/{DEVICE_UUID}/display"
    log_event(f"Pinging API: {endpoint}")
    
    # Add headers to reduce data transfer
    headers = {
        'Connection': 'close',
        'Accept-Encoding': 'gzip',
    }
    
    # Prepare request body with firmware version
    body = {
        "current_fw_ver": CURRENT_VERSION,
        "request_time_sync": True
    }
    
    try:
        response = requests.post(endpoint, json=body, headers=headers, timeout=HTTP_TIMEOUT)
        if response.status_code != 200:
            log_event(f"API returned non-200 status: {response.status_code}")
            return {}
            
        data = response.json()
        log_event(f"API response received: {json.dumps(data)[:100]}...")  # Log first 100 chars to avoid large logs
        
        # If we received time information, sync the system clock
        if "time" in data:
            sync_time_from_server(data["time"])
            
        return data
    except Exception as e:
        log_event(f"Exception during API ping: {e}")
        return {}

def sync_time_from_server(time_obj):
    """Sync system time using information from the server"""
    global rtc_last_sync
    
    # Only attempt sync if we have all required time fields
    required_fields = ["year", "month", "day", "hour", "minute", "second"]
    if not all(field in time_obj for field in required_fields):
        log_event("ERROR: Incomplete time information from server")
        return False
    
    try:
        # Format and validate time values
        year = int(time_obj["year"])
        # Adjust 2-digit year to 4-digit year (like 23 -> 2023)
        if year < 100:
            year += 2000
            
        month = int(time_obj["month"])
        day = int(time_obj["day"])
        hour = int(time_obj["hour"])
        minute = int(time_obj["minute"])
        second = int(time_obj["second"])
        
        # Validate time values
        if (year < 2023 or year > 2100 or 
            month < 1 or month > 12 or 
            day < 1 or day > 31 or
            hour > 23 or minute > 59 or second > 59):
            log_event("ERROR: Invalid time values from server")
            return False
        
        # Format the date command
        date_str = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
        
        # Set the system date
        subprocess.run(["sudo", "date", "-s", date_str], check=True)
        
        # Update timestamp of last sync
        rtc_last_sync = time.time()
        
        log_event(f"System time synchronized to: {date_str}")
        return True
        
    except Exception as e:
        log_event(f"ERROR: Failed to sync time: {e}")
        return False

def download_image(url, local_path):
    """
    Downloads image from URL to local path.
    Returns True on success, False on error.
    """
    log_event(f"Downloading image from {url}")
    try:
        # Add headers to reduce data transfer
        headers = {
            'Connection': 'close',
            'Accept-Encoding': 'gzip',
        }
        
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            log_event(f"Failed to download image; HTTP status: {resp.status_code}")
            return False
            
        with open(local_path, 'wb') as f:
            f.write(resp.content)
            
        log_event(f"Image downloaded to {local_path} ({len(resp.content)} bytes)")
        
        # Verify the downloaded file is valid
        try:
            with Image.open(local_path) as test_img:
                # Just accessing these properties will validate the image
                width, height = test_img.size
                format = test_img.format
                log_event(f"Verified image: {width}x{height} {format}")
        except Exception as e:
            log_event(f"Downloaded image is invalid: {e}")
            return False
        
        return True
    except Exception as e:
        log_event(f"Exception during image download: {e}")
        return False


# Remove optimize_image_processing function as it's no longer used

# Removed images_are_identical function

# -------------------------------------------------------------------------------
# Main Logic
# -------------------------------------------------------------------------------

def main():
    global DEVICE_UUID, retry_attempt
    
    # Initialize device UUID if not set
    if DEVICE_UUID is None:
        DEVICE_UUID = get_device_uuid()
    
    log_event(f"===== INKY DISPLAY SERVICE STARTING =====")
    log_event(f"Device UUID: {DEVICE_UUID}")
    log_event(f"Current version: {CURRENT_VERSION}")
    
    # Initialize display
    try:
        inky = auto()
        inky.set_border(inky.WHITE)
        log_event("Display initialized successfully")
    except Exception as e:
        log_event(f"ERROR: Display initialization failed: {e}")
        sys.exit(1)
    
    # Enable power saving features
    enable_power_savings()
    
    # Skip initializing display message
    set_state(STATE_INITIALIZING)
    
    # Check and connect WiFi
    set_state(STATE_CONNECTING_WIFI)
    if not check_wifi_connection():
        retry_attempt += 1
        delay = min(BASE_ERROR_RETRY_DELAY * (2 ** (retry_attempt - 1)), MAX_ERROR_RETRY_DELAY)
        display_message(inky, "WiFi Connection Failed", STATE_WIFI_ERROR)
        prepare_for_sleep(delay)
        sys.exit(0)
    
    # Reset retry counter on successful connection
    retry_attempt = 0
    
    # Ping API for image and wake interval
    api_data = ping_api()
    if not api_data:
        retry_attempt += 1
        delay = min(BASE_ERROR_RETRY_DELAY * (2 ** (retry_attempt - 1)), MAX_ERROR_RETRY_DELAY)
        display_message(inky, "API Connection Failed", STATE_API_ERROR)
        power_off_display(inky)
        prepare_for_sleep(delay)
        sys.exit(0)
    
    # Reset retry counter on successful API call
    retry_attempt = 0
    
    # Extract data from API response
    image_url = api_data.get("image_url", "")
    next_wake_secs = int(api_data.get("next_wake_secs", 3600))
    
    log_event(f"Next wake seconds from API: {next_wake_secs}")
    
    # Check for NO_REFRESH marker
    if image_url == NO_REFRESH_MARKER:
        log_event("Received NO_REFRESH marker; skipping image update")
        set_state(STATE_NO_REFRESH, message=f"Next update in {next_wake_secs}s")
        power_off_display(inky)
        prepare_for_sleep(next_wake_secs)
        sys.exit(0)
    
    # Download the image
    if not download_image(image_url, LOCAL_IMAGE_PATH):
        retry_attempt += 1
        delay = min(BASE_ERROR_RETRY_DELAY * (2 ** (retry_attempt - 1)), MAX_ERROR_RETRY_DELAY)
        display_message(inky, "Image Download Failed", STATE_DOWNLOAD_ERROR)
        power_off_display(inky)
        prepare_for_sleep(delay)
        sys.exit(0)
    
    # Render the new image
    try:
        img = Image.open(LOCAL_IMAGE_PATH)
        
        # Resize if needed to match the display dimensions
        if img.size != (inky.WIDTH, inky.HEIGHT):
            log_event(f"Resizing image from {img.size} to {inky.WIDTH}x{inky.HEIGHT}")
            img = img.resize((inky.WIDTH, inky.HEIGHT))
        
        inky.set_image(img)
        inky.show()
        set_state(STATE_DISPLAYING_IMAGE, message=f"Image from {image_url}")
        log_event(f"Image displayed successfully")
    except Exception as e:
        retry_attempt += 1
        delay = min(BASE_ERROR_RETRY_DELAY * (2 ** (retry_attempt - 1)), MAX_ERROR_RETRY_DELAY)
        log_event(f"Error rendering image: {e}")
        display_message(inky, "Image Rendering Failed", STATE_RENDER_ERROR)
        power_off_display(inky)
        prepare_for_sleep(delay)
        sys.exit(0)
    
    # Reset retry counter on successful display
    retry_attempt = 0
    
    # Prepare for sleep
    log_event(f"Sleeping for {next_wake_secs} seconds before next update")
    set_state(STATE_SLEEPING)
    power_off_display(inky)
    prepare_for_sleep(next_wake_secs)
    sys.exit(0)

# -------------------------------------------------------------------------------
# Main Entry Point
# -------------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log_event("Service stopped by user")
        flush_log_batch()
        sys.exit(0)
    except Exception as e:
        log_event(f"Unhandled exception: {e}")
        flush_log_batch()
        sys.exit(1)