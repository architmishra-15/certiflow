FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything (index.html, app folder, csvs, etc.)
COPY . .

# Run FastAPI via Uvicorn on Cloud Run's dynamic PORT
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
