# Debian bookworm provides a reproducible MuseScore 3 CLI for x86-64 and ARM64.
# The resulting .mscz is editable by recent MuseScore Studio versions.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MUSESCORE_BIN=/usr/bin/musescore3 \
    CAPELLABRIDGE_DATA=/data QT_QPA_PLATFORM=offscreen MU_QT_QPA_PLATFORM=offscreen
RUN apt-get update && apt-get install -y --no-install-recommends \
    musescore3 poppler-utils fonts-dejavu-core fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/capellabridge
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN groupadd -g 10001 scoreapp && useradd -u 10001 -g scoreapp -d /tmp -s /usr/sbin/nologin scoreapp \
 && mkdir -p /data/jobs /tmp/capellabridge-runtime \
 && chown -R scoreapp:scoreapp /data /tmp/capellabridge-runtime
USER scoreapp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8080","--workers","1","--proxy-headers","--forwarded-allow-ips","127.0.0.1"]
