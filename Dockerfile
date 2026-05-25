FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# SQLite data directory — mount a Railway Volume here for persistence
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "app.py"]
