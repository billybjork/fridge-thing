import io
import time
import os
from fastapi import APIRouter, Request, Response
from fastapi.responses import Response as FastAPIResponse
from PIL import Image

# Playwright imports
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

router = APIRouter()

# Adjust these constants as needed.
TARGET_WIDTH = 448
TARGET_HEIGHT = 600

@router.get("/api/nts_now_playing", name="convert_nts_now_playing")
async def convert_nts_now_playing(request: Request):
    """
    Fetches the NTS.live page, accepts cookies, reveals the 'now playing' element,
    screenshots it, and returns a 600x448 BMP image.
    """
    # We'll run everything in an async context via Playwright.
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={"width": TARGET_WIDTH, "height": 1000}  # Height is arbitrary; we'll crop later
            )

            # 1. Go to NTS.live
            await page.goto("https://www.nts.live/", timeout=30000)

            # 2. Dismiss Cookie Popup (if present)
            try:
                # If the cookie button doesn't appear within a couple seconds, we skip it
                await page.wait_for_selector("#onetrust-accept-btn-handler", timeout=3000)
                await page.click("#onetrust-accept-btn-handler")
                await page.wait_for_timeout(1000)  # Let the popup fade out
            except PlaywrightTimeout:
                pass  # No cookie popup found

            # 3. Reveal the "Now Playing" Module
            button_selector = (
                "#nts-live-header > div.live-header__channels--expanded.live-header__channels > "
                "div.live-header__footer.live-header__footer--collapsed.live-header__footer--mobile > "
                "button.live-header__footer__button"
            )
            await page.wait_for_selector(button_selector, timeout=10000)
            await page.click(button_selector)

            # 4. Wait for the content to appear
            content_selector = "#nts-live-header > div.live-header__channels--expanded.live-header__channels"
            await page.wait_for_selector(content_selector, timeout=10000)

            # Optionally scroll it into view
            await page.locator(content_selector).scroll_into_view_if_needed()
            await page.wait_for_timeout(2000)  # Let animations settle

            # 5. Screenshot the Element
            screenshot_path = "element.png"  # Temp file on disk (or do in-memory)
            await page.locator(content_selector).screenshot(path=screenshot_path)

            # 6. Crop Bottom 5 Pixels
            img = Image.open(screenshot_path)
            img_w, img_h = img.size
            if img_h > 5:
                img = img.crop((0, 0, img_w, img_h - 5))

            # 7. Letterbox to 600x448 (black background)
            final_img = Image.new("RGB", (TARGET_WIDTH, TARGET_HEIGHT), "black")
            cropped_w, cropped_h = img.size

            if cropped_h < TARGET_HEIGHT:
                offset_y = (TARGET_HEIGHT - cropped_h) // 2
                final_img.paste(img, (0, offset_y))
            elif cropped_h > TARGET_HEIGHT:
                top_crop = (cropped_h - TARGET_HEIGHT) // 2
                img_cropped = img.crop((0, top_crop, cropped_w, top_crop + TARGET_HEIGHT))
                final_img.paste(img_cropped, (0, 0))
            else:
                # Perfect fit
                final_img.paste(img, (0, 0))

            # 8. Convert to BMP in-memory
            output_buffer = io.BytesIO()
            final_img.save(output_buffer, format="BMP")
            bmp_data = output_buffer.getvalue()

            await browser.close()

        # Return the image as BMP
        return FastAPIResponse(content=bmp_data, media_type="image/bmp")

    except Exception as e:
        print("Error capturing NTS now playing:", e)
        return Response("Internal server error", status_code=500)