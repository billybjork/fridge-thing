from typing import Optional
import asyncpg

async def get_or_create_device(conn: asyncpg.Connection, device_uuid: str):
    row = await conn.fetchrow("""
        SELECT * FROM devices WHERE device_uuid = $1
    """, device_uuid)
    if not row:
        device_id = await conn.fetchval("""
            INSERT INTO devices (device_uuid) VALUES ($1) RETURNING id
        """, device_uuid)
        return {"id": device_id, "device_uuid": device_uuid, "next_wake_secs": 3600, "latest_image_url": None}
    return dict(row)

async def update_device_image(conn: asyncpg.Connection, device_id: int, image_url: str):
    await conn.execute("""
        UPDATE devices SET latest_image_url = $1, updated_at = now() WHERE id = $2
    """, image_url, device_id)

async def log_event(conn: asyncpg.Connection, device_id: int, event_type: str, message: str = ""):
    await conn.execute("""
        INSERT INTO device_logs (device_id, event_type, message)
        VALUES ($1, $2, $3)
    """, device_id, event_type, message)