FROM ubuntu:22.04

# Prevent prompts during install
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git curl wget unzip gnupg ca-certificates \
    fonts-liberation libasound2 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 \
    libnspr4 libnss3 libx11-xcb1 libxcomposite1 libxdamage1 \
    libxrandr2 libxshmfence1 libxss1 libxtst6 libxkbcommon0 \
    libxcb1 libxext6 libxfixes3 libxrender1 xdg-utils && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml .
COPY uv.lock .
RUN VIRTUALENV= uv sync --locked
RUN uv run playwright install chromium

COPY src ./src
CMD ["uv", "run", "python", "-m", "src.scrape"]
