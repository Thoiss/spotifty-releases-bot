# Small, reproducible image: the bot is pure Python plus 'requests'.
FROM python:3.12-slim

# Python housekeeping:
#   PYTHONUNBUFFERED   -> logs appear in 'docker logs' immediately
#   PYTHONDONTWRITEBYTECODE -> no .pyc clutter in the image
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Dependencies first, so editing source code does not invalidate this layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Run as a non-root user. The numeric id matches the volume permissions set in
# docker-compose.yml; change both together if your host needs something else.
RUN useradd --create-home --uid 10001 botuser \
    && mkdir -p /data \
    && chown -R botuser:botuser /data /app
USER botuser

VOLUME ["/data"]

# 'run' keeps the container alive and performs the check every night.
ENTRYPOINT ["python", "-m", "spotify_release_bot"]
CMD ["run"]
