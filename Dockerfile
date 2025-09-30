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

COPY pyproject.toml ./
RUN pip install --no-cache-dir -U pip
RUN pip install --no-cache-dir .

COPY ./bot /app/bot

RUN mkdir -p /app/data/uploads /app/data/games /app/data/patches

CMD ["python", "-m", "bot"]