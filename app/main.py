from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import batches, templates
from fastapi.responses import HTMLResponse
from pathlib import Path

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/v1/auth/me")
def mock_auth():
    return {"username": "admin", "role": "admin", "csrf_token": "mock-token-123"}
