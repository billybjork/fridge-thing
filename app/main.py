# Start dev server: python -m app.main

import asyncpg
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.config import DATABASE_URL, SERVER_HOST, SERVER_PORT
from app.routes.devices import router as devices_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create an asyncpg pool with statement_cache_size=0
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
    yield
    # Shutdown: Close the connection pool
    await app.state.pool.close()

app = FastAPI(title="Fridge Thing API", lifespan=lifespan)

# Include router
app.include_router(devices_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)