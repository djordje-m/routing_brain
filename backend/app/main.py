"""FastAPI app: startup wiring (pool + schema) and the health route only."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import apply_schema, get_pool
from app.seed_trucks import seed_trucks


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await get_pool()
    await apply_schema(pool)
    await seed_trucks(pool)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
