# Build from the repository ROOT so the schema ships inside the image:
#   docker build -f backend/Dockerfile .
FROM python:3.12-slim

# tesseract = OCR engine, poppler = PDF rasterizer for scanned documents
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY db-init/ ./db-init/

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
