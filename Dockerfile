# MuseScore 2.3.2 is pinned for the x86-64 legacy CAP decode boundary because
# MuseScore 3 compiles out CAP chord articulations. MuseScore 3 remains the
# canonical writer/exporter, and CAPX never enters the legacy importer.
FROM python:3.12-slim-bookworm
ARG MUSESCORE2_URL=https://ftp.osuosl.org/pub/musescore/releases/MuseScore-2.3.2/MuseScore-2.3.2-x86_64.AppImage
ARG MUSESCORE2_SHA256=e50e133bc7b3d32363f5b39bd4ec4542cc93590993d981d28f3b282f00e083b5
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MUSESCORE_BIN=/usr/bin/musescore3 \
    MUSESCORE_LEGACY_CAP_BIN=/opt/musescore2/AppRun CAPELLABRIDGE_DATA=/data \
    QT_QPA_PLATFORM=offscreen MU_QT_QPA_PLATFORM=offscreen
RUN apt-get update && apt-get install -y --no-install-recommends \
    musescore3 poppler-utils fonts-dejavu-core fonts-liberation ca-certificates \
    curl libfuse2 \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --retry 3 --silent --show-error \
      "$MUSESCORE2_URL" --output /tmp/musescore2.AppImage \
 && echo "$MUSESCORE2_SHA256  /tmp/musescore2.AppImage" | sha256sum --check \
 && chmod +x /tmp/musescore2.AppImage \
 && cd /tmp \
 && ./musescore2.AppImage --appimage-extract >/dev/null \
 && mv /tmp/squashfs-root /opt/musescore2 \
 && rm /tmp/musescore2.AppImage \
 && test -x /opt/musescore2/AppRun \
 && QT_QPA_PLATFORM=offscreen /opt/musescore2/AppRun --version >/dev/null
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
