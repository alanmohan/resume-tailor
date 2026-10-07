"""Job endpoints: POST /jobs, GET /jobs, GET /jobs/{job_id},
PATCH /jobs/{job_id}. Included by app/main.py."""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["jobs"])
