"""Evidence endpoint: GET /evidence/{evidence_id}. Included by app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["evidence"])
