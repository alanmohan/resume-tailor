"""Generation endpoints: POST /generations, GET /generations,
GET/PATCH /generations/{generation_id}, POST .../validate and
POST .../items/{item_id}/regenerate. Included by app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["generations"])
