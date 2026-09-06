FROM python:3.11-slim

# 只保留 trixie 主源和 security 源，去掉 trixie-updates（该端点在部分网络下不稳定）
RUN printf 'deb http://deb.debian.org/debian trixie main\ndeb http://deb.debian.org/debian-security trixie-security main\n' \
        > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Use Aliyun PyPI mirror to avoid SSL issues with pypi.org
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip config set global.trusted-host mirrors.aliyun.com

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Unprivileged user for defence-in-depth. The image default stays root for
# the legacy deployment; compose.in1.yml opts into this user explicitly.
RUN useradd -m -u 1001 files

EXPOSE 8100

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
