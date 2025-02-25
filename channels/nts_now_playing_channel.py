import io
import os
from fastapi import APIRouter, Request, Response
from fastapi.responses import Response as FastAPIResponse
from PIL import Image

# Playwright imports
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

router = APIRouter()

@router.get("/api/nts_now_playing", name="convert_nts_now_playing")
async def convert_nts_now_playing(request: Request, width: int = 600, height: int = 448):
    """
    Fetches the NTS.live page, dismisses cookie popups, reveals the "Now Playing" element,
    screenshots it, and returns a BMP image dynamically resized to the device's resolution.

    Query Parameters:
      - width: Desired display width (default 600)
      - height: Desired display height (default 448)

    This endpoint uses Playwright to capture the now-playing element and adapts the image.
    """
    try:
        async with async_playwright() as p:
            # Launch browser with dynamic viewport width; use fixed capture height for completeness.
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={"width": width, "height": 800}  # Fixed capture height ensures complete rendering.
            )

            # 1. Navigate to NTS.live.
            await page.goto("https://www.nts.live/", timeout=30000)

            # 2. Dismiss the cookie popup if it appears.
            try:
                await page.wait_for_selector("#onetrust-accept-btn-handler", timeout=3000)
                await page.click("#onetrust-accept-btn-handler")
                await page.wait_for_timeout(1000)  # Allow time for the popup to fade.
            except PlaywrightTimeout:
                pass  # Cookie popup not found.

            # 3. Reveal the "Now Playing" module.
            button_selector = (
                "#nts-live-header > div.live-header__channels--expanded.live-header__channels > "
                "div.live-header__footer.live-header__footer--collapsed.live-header__footer--mobile > "
                "button.live-header__footer__button"
            )
            await page.wait_for_selector(button_selector, timeout=10000)
            await page.click(button_selector)

            # 4. Wait for the content to appear and scroll it into view.
            content_selector = "#nts-live-header > div.live-header__channels--expanded.live-header__channels"
            await page.wait_for_selector(content_selector, timeout=10000)
            await page.locator(content_selector).scroll_into_view_if_needed()
            await page.wait_for_timeout(2000)  # Let any animations settle.

            # 5. Screenshot the now-playing element.
            screenshot_path = "element.png"  # Temporary file (could be in-memory if desired).
            await page.locator(content_selector).screenshot(path=screenshot_path)

            # 6. Crop the bottom 5 pixels (if needed).
            img = Image.open(screenshot_path)
            img_w, img_h = img.size
            if img_h > 5:
                img = img.crop((0, 0, img_w, img_h - 5))

            # 7. Letterbox the screenshot to the target resolution (width x height) with a black background.
            final_img = Image.new("RGB", (width, height), "black")
            cropped_w, cropped_h = img.size

            if cropped_h < height:
                offset_y = (height - cropped_h) // 2
                final_img.paste(img, (0, offset_y))
            elif cropped_h > height:
                top_crop = (cropped_h - height) // 2
                img_cropped = img.crop((0, top_crop, cropped_w, top_crop + height))
                final_img.paste(img_cropped, (0, 0))
            else:
                final_img.paste(img, (0, 0))

            # 8. Convert the final image to BMP in-memory.
            output_buffer = io.BytesIO()
            final_img.save(output_buffer, format="BMP")
            bmp_data = output_buffer.getvalue()

            await browser.close()

        return FastAPIResponse(content=bmp_data, media_type="image/bmp")

    except Exception as e:
        print("Error capturing NTS now playing:", e)
        return Response("Internal server error", status_code=500)