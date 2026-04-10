FROM python:3.11-slim

WORKDIR /app

# System deps for pandas / scipy
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Auto-create runtime directories
RUN mkdir -p data logs

EXPOSE 8501 8080
