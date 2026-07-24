FROM python:3.12.1-slim

# System deps: poppler (PDF -> image for OCR) + tesseract (OCR engine, English + Hindi)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-hin \
    tesseract-ocr-eng \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Render sets $PORT automatically; gunicorn binds to it at runtime
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT:-8000} --timeout 180 app:app"]
