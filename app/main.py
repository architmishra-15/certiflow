from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import batches, templates
from fastapi.responses import HTMLResponse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(
    Path(__file__).resolve().parent.parent / ".env",
    override=False,
)

app = FastAPI(
    title="Certificate Automation API",
    version="1.0.0",
    description="Hackathon backend implementing Polars data processing and PyMuPDF template generation."
)

BASE_DIR = Path(__file__).resolve().parent.parent # Points to the root folder where index.html lives

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = BASE_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "<h1>index.html not found</h1>", 404

app.include_router(batches.router)
app.include_router(batches.jobs_router)
app.include_router(templates.router)

import os
from fastapi.middleware.cors import CORSMiddleware

frontend_origins = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "").split(",")
    if origin.strip()
]

if frontend_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=frontend_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

@app.get("/api/v1/auth/me")
def mock_auth():
    return {"username": "admin", "role": "admin", "csrf_token": "mock-token-123"}
