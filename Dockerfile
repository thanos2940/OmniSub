# Omnisub - Multi-stage Docker Build
# Stage 1: Build Frontend
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Backend Runtime
FROM python:3.11-slim

WORKDIR /app

# ffmpeg/ffprobe: used to probe media containers for muxed .ass subtitle tracks and
# extract them to sidecars (docs/PLAN_embedded_ass_extraction.md). Optional at runtime
# — the feature reports itself unavailable without it — but shipping it here means the
# container works out of the box. --no-install-recommends keeps this to the codecs.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install a CPU-only torch build before requirements.txt pulls in
# sentence-transformers (which otherwise drags in the full CUDA build —
# several GB unused in a container with no GPU). pip sees this requirement
# already satisfied and won't replace it when requirements.txt is installed
# next, as long as the pinned version here satisfies sentence-transformers'
# constraint.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ ./

# Copy built frontend — served by main.py's StaticFiles mount + SPA fallback.
COPY --from=frontend-builder /app/frontend/dist ./static

# Expose port
EXPOSE 8000

# Persistent state (config.json, omnisub*.db, projects/, translation_memory/)
# lives here so it survives image rebuilds — mount a volume at /config.
# See docs/PLAN_docker_deployment.md.
ENV OMNISUB_DATA_DIR=/config
VOLUME /config

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Health check — urllib instead of `requests` (not a declared dependency);
# /api/health is intentionally distinct from bare /health, which the SPA
# fallback owns for the frontend's own Health page.
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Run server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
