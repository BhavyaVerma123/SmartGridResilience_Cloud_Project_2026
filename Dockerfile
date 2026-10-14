# ─────────────────────────────────────────────────────────────
# Dockerfile — Smart Grid Resilience Backend
# Course  : BCSE355L — Cloud Architecture Design
# Deploys : FastAPI + ML Agents on AWS Fargate
# ─────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV AWS_DEFAULT_REGION=us-east-1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source code
COPY src/ ./src/
COPY dataset/processed/ ./dataset/processed/
COPY results/ ./results/

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/agents/status || exit 1

# Run the FastAPI server
CMD ["python", "-m", "uvicorn", "src.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
