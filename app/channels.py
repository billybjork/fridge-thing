import random
import asyncpg

async def get_random_image_url(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow("""
        SELECT image_s3_object_url
        FROM assets
        ORDER BY random()
        LIMIT 1
    """)
    if row:
        return row["image_s3_object_url"]
    # Fallback if no row
    return "https://some-default-url.bmp"