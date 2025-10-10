FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    fuse \
    libfuse2 \
    git \
    build-essential \
    tk-dev \
    && rm -rf /var/lib/apt/lists/*
    
WORKDIR /app

RUN python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

RUN python3 -m pip install --upgrade pip setuptools wheel

RUN git clone https://github.com/ArchipelagoMW/Archipelago.git /opt/archipelago
RUN sed -i'.bak' 's/ModuleUpdate.update()/pass # ModuleUpdate.update()/' /opt/archipelago/Generate.py
RUN find /opt/archipelago -type f -name "requirements.txt" | xargs -n 1 pip install --no-cache-dir -r

COPY pyproject.toml pdm.lock ./
RUN pip install --no-cache-dir .

COPY ./bot /app/bot

RUN mkdir -p /app/data/uploads /app/data/games /app/data/patches

CMD ["python", "-m", "bot"]