# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS engine
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY packages/contracts/model-source.json ./model-source.json
COPY patches/0001-apple-silicon.patch ./engine.patch
# Checkout the reviewed source and apply its patch without requiring a Git identity.
RUN python - <<'PY'
import json, subprocess
from pathlib import Path
source = json.loads(Path('model-source.json').read_text())
repo = '/engine'
subprocess.run(['git', 'init', repo], check=True)
subprocess.run(['git', '-C', repo, 'fetch', '--depth=1', source['repository'], source['base_commit']], check=True)
subprocess.run(['git', '-C', repo, 'checkout', '--detach', 'FETCH_HEAD'], check=True)
subprocess.run(['git', '-C', repo, 'apply', '/build/engine.patch'], check=True)
PY
RUN rm -rf /engine/.git

FROM python:3.12-slim-bookworm AS server
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1 \
    SONGCUT_ROOT=/app SONGCUT_DATA=/data SONGCUT_UPSTREAM=/app/upstream/AutoSlicer-v2 \
    SONGCUT_HOST=0.0.0.0 SONGCUT_PORT=8765 SONGCUT_SERVER=1 SONGCUT_DEVICE=cpu \
    SONGCUT_FFMPEG=/usr/bin/ffmpeg SONGCUT_FFPROBE=/usr/bin/ffprobe
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-noto-cjk ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 songcut && useradd --uid 10001 --gid songcut --create-home songcut \
    && mkdir /data && chown songcut:songcut /data
WORKDIR /app
# CPU wheels keep the default image independent of NVIDIA drivers/CUDA downloads.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 torchaudio==2.5.1 \
    && pip install 'numpy>=1.26,<3' 'scipy>=1.12,<2' 'requests>=2.32,<3' 'tensorboard>=2.18,<3'
COPY services/studio/ /app/services/studio/
RUN pip install ./services/studio
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
COPY docs/ /app/docs/
COPY packages/ /app/packages/
COPY --from=engine /engine/ /app/upstream/AutoSlicer-v2/
COPY --from=web /web/dist/ /app/apps/web/dist/
USER songcut
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"
CMD ["python", "-m", "studio.api"]
