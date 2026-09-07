FROM python:3.11-slim

WORKDIR /app

# tgcrypto needs a C compiler to build from source on some platforms
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY stream_server.py .

# Railway/Render inject PORT at runtime; default to 8080 for local/VPS runs
ENV PORT=8080
EXPOSE 8080

CMD ["python", "stream_server.py"]
