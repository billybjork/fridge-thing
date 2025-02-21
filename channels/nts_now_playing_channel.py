import io
import time
import os
from fastapi import APIRouter, Request, Response
from fastapi.responses import Response as FastAPIResponse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from PIL import Image

router = APIRouter()

# Adjust these constants as needed.
TARGET_WIDTH = 600
TARGET_HEIGHT = 448

@router.get("/api/nts_now_playing", name="convert_nts_now_playing")
async def convert_nts_now_playing(request: Request):
    """
    Fetches the NTS.live page, accepts cookies, reveals the 'now playing' element,
    screenshots it, and returns a 600x448 BMP image.
    """
    # -----------------------------
    # 1. Set up Headless Selenium
    # -----------------------------
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument(f"--window-size={TARGET_WIDTH},800")  # Height is arbitrary
    # If your server environment requires a custom driver path, set it here:
    # driver_path = "/usr/bin/chromedriver"  # Example
    # driver = webdriver.Chrome(options=chrome_options, executable_path=driver_path)
    
    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        driver.get("https://www.nts.live/")
        wait = WebDriverWait(driver, 20)

        # -----------------------------
        # 2. Dismiss Cookie Popup
        # -----------------------------
        try:
            cookie_button = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "#onetrust-accept-btn-handler"))
            )
            cookie_button.click()
            time.sleep(1)  # Let the popup fade out
        except:
            # If it's not there, no problem
            pass

        # -----------------------------
        # 3. Reveal the 'Now Playing' Module
        # -----------------------------
        button_selector = (
            "#nts-live-header > div.live-header__channels--expanded.live-header__channels > "
            "div.live-header__footer.live-header__footer--collapsed.live-header__footer--mobile > "
            "button.live-header__footer__button"
        )
        button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, button_selector)))
        button.click()

        # Wait for the content to appear
        content_selector = "#nts-live-header > div.live-header__channels--expanded.live-header__channels"
        content_element = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, content_selector)))

        # Optionally scroll it into view
        driver.execute_script("arguments[0].scrollIntoView(true);", content_element)
        time.sleep(2)  # Let any animations settle

        # -----------------------------
        # 4. Screenshot the Element
        # -----------------------------
        screenshot_path = "element.png"  # Temporarily on disk (you can also do in-memory if desired)
        content_element.screenshot(screenshot_path)

        # -----------------------------
        # 5. Crop the Bottom 5 Pixels
        # -----------------------------
        img = Image.open(screenshot_path)
        img_w, img_h = img.size
        # Crop off bottom 5 pixels (if image is tall enough)
        if img_h > 5:
            img = img.crop((0, 0, img_w, img_h - 5))

        # -----------------------------
        # 6. Letterbox to 600x448
        # -----------------------------
        # If final height is less than 448, we center it on black background
        # If it's taller than 448, we crop from the center.
        final_img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), "black")
        cropped_w, cropped_h = img.size

        if cropped_h < TARGET_HEIGHT:
            offset_y = (TARGET_HEIGHT - cropped_h) // 2
            final_img.paste(img, (0, offset_y))
        elif cropped_h > TARGET_HEIGHT:
            # Crop from center
            top_crop = (cropped_h - TARGET_HEIGHT) // 2
            img_cropped = img.crop((0, top_crop, cropped_w, top_crop + TARGET_HEIGHT))
            final_img.paste(img_cropped, (0, 0))
        else:
            # Perfect fit
            final_img.paste(img, (0, 0))

        # -----------------------------
        # 7. Convert to BMP in-memory
        # -----------------------------
        output_buffer = io.BytesIO()
        final_img.save(output_buffer, format="BMP")
        bmp_data = output_buffer.getvalue()

        # Return the image as BMP
        return FastAPIResponse(content=bmp_data, media_type="image/bmp")

    except Exception as e:
        print("Error capturing NTS now playing:", e)
        return Response("Internal server error", status_code=500)

    finally:
        driver.quit()