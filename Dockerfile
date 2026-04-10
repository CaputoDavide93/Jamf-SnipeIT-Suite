# Jamf-SnipeIT Suite
# Multi-stage build for optimized image size
# Supports both AMD64 (Intel) and ARM64 (Apple Silicon M1/M2/M3)

# Stage 1: Builder (must match target platform for binary extensions like pydantic_core)
FROM python:3.13-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# Stage 2: Runtime
FROM python:3.13-slim

LABEL maintainer="Davide Caputo <CaputoDav@gmail.com>"
LABEL description="Jamf-SnipeIT Suite - Unified Asset Management Tool"
LABEL version="1.0.0"

# Create non-root user for security
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY src/ ./src/

# Copy non-secret config files (mapping tables, model maps)
COPY config/equipment_mapping.json ./config/equipment_mapping.json
COPY config/model_map.json* ./config/

# Copy entrypoint script
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Create directories for config and logs
RUN mkdir -p /app/config /app/logs /app/output && \
    chown -R appuser:appgroup /app

# Set Python path
ENV PYTHONPATH="/app/src"
ENV PYTHONUNBUFFERED=1

# Default run mode (scheduler with startup run)
ENV RUN_MODE=scheduler

# Switch to non-root user
USER appuser

# Default command: run scheduler with startup execution
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Health check - hit the internal health server (started by docker_scheduler.py)
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=5)" || exit 1
