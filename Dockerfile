FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Persistence is external (Supabase Postgres via DATABASE_URL) — no local data dir needed.
ENV HOST=0.0.0.0

EXPOSE 8000

CMD ["python", "app.py"]
