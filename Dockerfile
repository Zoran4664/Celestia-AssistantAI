# =============================================================
# 主文件与主应用 — Docker 运行镜像
# 基础镜像 Python 3.11（chromadb 0.5.3 + numpy 1.26 最稳定）
# =============================================================
FROM python:3.11-slim

WORKDIR /app

# Qt 运行所需系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libegl1 \
    libfontconfig1 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcb-cursor0 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

# GUI 需 X11/Wayland 转发（见 docker-compose.yml）
CMD ["python", "main.py"]
