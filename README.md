# Certificate Automation Backend

Hackathon backend implementing the core API contract. Built with FastAPI (async routing), Polars (high-performance CSV/Excel ingestion), PyMuPDF (PDF placeholder injection), and aiosmtplib (async mailing).

## Setup
1. `pip install -r requirements.txt`
2. `uvicorn app.main:app --reload`
3. Check the auto-generated Swagger UI docs at `http://127.0.0.1:8000/docs`
