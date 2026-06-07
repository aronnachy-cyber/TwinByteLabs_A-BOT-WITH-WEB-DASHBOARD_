FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY SENTINEL-BOT-Main/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY SENTINEL-BOT-Main/ ./

ENV PORT=5000
ENV START_BOT_IN_WEB=true
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "--reload", "wsgi:app"]
