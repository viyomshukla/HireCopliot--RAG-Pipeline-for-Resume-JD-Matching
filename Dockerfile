FROM python:3.11-slim

# Some Python wheels compile from source on slim images. Without a compiler the
# pip install fails partway through with an unhelpful error.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies are copied and installed before the application code, so a code
# change does not invalidate the pip layer and rebuild everything.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the model weights at BUILD time, not at first request. Otherwise the
# first visitor waits 30 seconds while ~220MB downloads, and the app has a
# runtime dependency on HuggingFace being reachable.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" && \
    python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')"

COPY backend/ ./backend/
COPY frontend/dist/ ./frontend/dist/

WORKDIR /app/backend

# 7860 is the port HuggingFace Spaces expects.
EXPOSE 7860
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7860"]