# PaperTrail: API + demo frontend with server-side Tesseract. Serves on port 7860.
#   docker build -t papertrail .
#   docker run --rm -p 7860:7860 --env-file .env papertrail
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[ocr,serve]"

COPY app.py ./
COPY scripts ./scripts
COPY docs ./docs
COPY public ./public
COPY bench/results ./bench/results
RUN python scripts/build_web.py && chown -R app:app /app

USER app
ENV PORT=7860 \
    PAPERTRAIL_CACHE_DIR=/tmp/papertrail-cache \
    PYTHONUNBUFFERED=1
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:7860/api/health')"
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
