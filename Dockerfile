# Build the pinned CAP decoder from source. The upstream 2.3.2 importer can
# dereference invalid grace-note anchors and cannot resolve a terminal volta
# endpoint stored just beyond the last rhythmic event (Bogey.cap). The patch
# validates every spanner anchor and repairs only that terminal-volta sentinel.
# MuseScore 3 remains the canonical writer/exporter, and CAPX never enters this
# legacy boundary.
FROM debian:bookworm-slim AS musescore2-builder
ARG MUSESCORE2_SOURCE_URL=https://github.com/musescore/MuseScore/archive/refs/tags/v2.3.2.tar.gz
ARG MUSESCORE2_SOURCE_SHA256=8d84a081ca03bdb0bbf706c2848064f27c3a6331033e313954133d44fb769aee
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates cmake curl patch pkg-config xvfb xauth \
    qtbase5-dev qtdeclarative5-dev qttools5-dev qttools5-dev-tools qtscript5-dev \
    libqt5opengl5-dev libqt5svg5-dev libqt5xmlpatterns5-dev libfreetype6-dev \
    libsndfile1-dev zlib1g-dev libssl-dev libgl1-mesa-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --retry 3 --silent --show-error \
      "$MUSESCORE2_SOURCE_URL" --output /tmp/musescore2.tar.gz \
 && echo "$MUSESCORE2_SOURCE_SHA256  /tmp/musescore2.tar.gz" | sha256sum --check \
 && mkdir -p /src/musescore2 \
 && tar -xzf /tmp/musescore2.tar.gz --strip-components=1 -C /src/musescore2 \
 && rm /tmp/musescore2.tar.gz
COPY patches/musescore-2.3.2-cap-spanner-safety.patch /tmp/cap-spanner-safety.patch
RUN cd /src/musescore2 \
 && patch -p1 < /tmp/cap-spanner-safety.patch \
 && cmake -S . -B build.release \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_FLAGS=-fpermissive \
      -DCMAKE_INSTALL_PREFIX=/opt/musescore2 \
      -DBUILD_ALSA=OFF -DBUILD_JACK=OFF -DBUILD_LAME=OFF \
      -DBUILD_PORTAUDIO=OFF -DBUILD_PORTMIDI=OFF \
      -DBUILD_PULSEAUDIO=OFF -DBUILD_WEBKIT=OFF \
      -DDOWNLOAD_SOUNDFONT=OFF -DZERBERUS=OFF -DOSC=OFF \
      -DSOUNDFONT3=OFF -DHAS_AUDIOFILE=OFF \
      -DUSE_SYSTEM_FREETYPE=ON \
 && cmake --build build.release --target mscore mscore_alias manpages --parallel 2 \
 && cmake --install build.release \
 && mkdir -p /tmp/cap-smoke \
 && for source in /src/musescore2/test/cap/*.cap; do \
      name="$(basename "$source" .cap)"; \
      xvfb-run -a -s "-screen 0 1024x768x24 -nolisten tcp" \
        /opt/musescore2/bin/mscore -o "/tmp/cap-smoke/$name.mscx" "$source"; \
      test -s "/tmp/cap-smoke/$name.mscx"; \
    done

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MUSESCORE_BIN=/usr/bin/musescore3 \
    MUSESCORE_LEGACY_CAP_BIN=/usr/local/bin/musescore2-cap CAPELLABRIDGE_DATA=/data \
    QT_QPA_PLATFORM=offscreen MU_QT_QPA_PLATFORM=offscreen
RUN apt-get update && apt-get install -y --no-install-recommends \
    musescore3 poppler-utils fonts-dejavu-core fonts-liberation ca-certificates \
    libxcomposite1 libxcursor1 libxdamage1 libxi6 libxrandr2 libxtst6 \
    libcap2 liborc-0.4-0 libxslt1.1 xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
COPY --from=musescore2-builder /opt/musescore2 /opt/musescore2
RUN test -x /opt/musescore2/bin/mscore
COPY scripts/musescore2-cap /usr/local/bin/musescore2-cap
RUN chmod 0755 /usr/local/bin/musescore2-cap \
 && /usr/local/bin/musescore2-cap --version >/dev/null
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
