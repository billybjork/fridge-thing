from fastapi import APIRouter, Request, HTTPException
from app.schemas import DeviceDisplayRequest, DeviceDisplayResponse
from app.crud import get_or_create_device, log_event
from app.channels import get_random_image_url
from app.crud import update_device_image
import os

router = APIRouter()

@router.post("/api/display", response_model=DeviceDisplayResponse)
async def get_display(request_data: DeviceDisplayRequest, request: Request):
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        device_row = await get_or_create_device(conn, request_data.device_uuid)
        device_id = device_row["id"]
        
        # Log an event
        await log_event(conn, device_id, "wake",
                        f"current_fw_ver={request_data.current_fw_ver}")
        
        # Return whatever is in image_url
        image_url = device_row.get("image_url") or "https://s3.us-west-1.amazonaws.com/bjork.love/test.bmp?response-content-disposition=inline&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Security-Token=IQoJb3JpZ2luX2VjEMz%2F%2F%2F%2F%2F%2F%2F%2F%2F%2FwEaCXVzLXdlc3QtMSJGMEQCIA%2BozLOLcON5f%2B3vcTJDgNJ9DxsSi6Ov0CIDcfhy1%2BPSAiB%2BGGz7j4nj35cr52Pixi9Jiz%2BO%2FuZ%2FVqlP2wH5xX33iirUAwjl%2F%2F%2F%2F%2F%2F%2F%2F%2F%2F8BEAAaDDk3NTA0OTg4NzI5NSIMaoM%2FeUoWinEerrm1KqgDgwcdmDnZFkwiMoZwGPpGkgwWoAf%2FdSWxylkCUNKvtvTfiZKofGPc3NGnYTxEgKziJmGKzsM7wNODfo52BRbcbTNJjzXAczqm5JExysH%2FwVB1HmzQOxpJtHAJyJ7DOLVstuUkT4WoFqmUodyYJg3fkpEKUgmhAUXLer2BKBbd4SxmkgI7GB7jW8sf1zwyhHroxRB9slEG9vDa3wLD4XV2CTFLDWf5%2B3fDRMpVhs8gu2sOA8oL0cfb%2FbIQqrUvUO2f0O2er%2FUCnHO1w%2BdQpmNjc3E0EXHzCgK3H2eTR0zw6lE7HE%2FQlPc1kbhANIfBGRhJSnwpc%2Fr2IqxvAkRazL1kDkJkFjf7aMstgPVUEcYhulxAz5OX2KbrzWo0naT069jZ4hoDTQ%2FftclSwv63kZjixnyt1DjVVTN0f4xzVskU0rZ5yH8iuBdBUPxe2ufQ%2FIG4mUZoEmY7J4VuPBoNYnnUt20GgJ8ltkfDm2aXXCWSzAzRpER%2FeBqX9rpnswBbqq1c9NXHQyMP6iigeBAqGLuqHIQMVAMu9CmEO7z4TGgC3W3qXGnwqbRnpDCN8K%2B9BjrlAjgbqCNvr2XxPUYjZ%2FxDEViEAcMWZz2WtT8617vBcf6BBD4uszlx5P6OeIzPfAlzEvBlWubG7i8cuTZFVlYcFYG%2BMjK6pNjHaBIrRQlkdEkmm%2FmPcLKCpBou41haMxXM1sDx0ra7oy7Ivg2qmcTG8TGMZ6JeN7mXhREws36U1dvDw5U57AaPduTRa1Qxqzsx2VoeyOtNJvQRVpBEkEmtzFbw1ewvHoy%2Fjo0SZP13cWehWgSy5E4rBeEBntsNL78xv1enNjOPJ7jgl%2BrFUgEW95HxF%2B9fLX3KxGqM%2BKjbaL1xkT0SlLMJBisLonG%2FmjCR4jn5wquL6Ru%2BvFRFiUXfhIBzGrfGWL4u0C01oJBuBpB%2BkOlKsA6xEXjvHj%2FpwBbM9IWrrr6DZhN7VDmEaClhrWCgsL6E4WxQsqm2n1M1aehbefLeMoXkjoMdxAuqDV1aBRI7zATrrsVQEHVpZNb5eGRfrXCJZQ%3D%3D&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIA6GBMARY74AQ6RVUP%2F20250212%2Fus-west-1%2Fs3%2Faws4_request&X-Amz-Date=20250212T033051Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Signature=858ca44f414ab107ff05bc40ed0bbac0781bf9975588781ae8f4ce353a25d0c5"
        next_wake_secs = device_row.get("next_wake_secs", 3600)

        return DeviceDisplayResponse(
            image_url=image_url,
            next_wake_secs=next_wake_secs
        )
    
@router.post("/api/devices/{device_uuid}/refresh")
async def refresh_device(device_uuid: str, request: Request):
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        device_row = await get_or_create_device(conn, device_uuid)
        device_id = device_row["id"]

        # For now: pick a random image
        random_url = await get_random_image_url(conn)
        
        # Update the device table with the new image
        await update_device_image(conn, device_id, random_url)
        
        return {"status": "ok", "image_url": random_url}