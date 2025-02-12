from pydantic import BaseModel

class DeviceDisplayRequest(BaseModel):
    device_uuid: str
    current_fw_ver: str | None = None  # ignoring OTA for now
    battery_voltage: float | None = None
    wifi_signal: int | None = None

class DeviceDisplayResponse(BaseModel):
    image_url: str
    next_wake_secs: int
    # ignoring firmware updates for now
