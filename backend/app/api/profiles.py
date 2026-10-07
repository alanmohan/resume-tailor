"""Profile endpoints: POST /profiles/ingest, GET /profile, PATCH /profile,
POST /profile/confirm. Included by app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["profile"])
