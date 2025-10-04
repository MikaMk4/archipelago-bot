FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN apt-get update && apt-get install -y --no-install-recommends \
    fuse \
    libfuse2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ADD https://github.com/ArchipelagoMW/Archipelago/releases/download/0.6.3/Archipelago_0.6.3_linux-x86_64.AppImage /opt/archipelago/Archipelago.AppImage
RUN chmod +x /opt/archipelago/Archipelago.AppImage

RUN cd /opt/archipelago && ./Archipelago.AppImage --appimage-extract

RUN test -f /opt/archipelago/squashfs-root/opt/Archipelago/ArchipelagoGenerate || (echo "ERROR: ArchipelagoGenerate was not found after unpacking!" && exit 1)

COPY pyproject.toml pdm.lock ./
RUN pip install --no-cache-dir pdm
RUN pdm install --prod --no-editable

COPY ./bot /app/bot

RUN mkdir -p /app/data/uploads /app/data/games /app/data/patches

CMD ["pdm", "run", "python", "-m", "bot"]