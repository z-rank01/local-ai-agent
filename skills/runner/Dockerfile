FROM python:3.11-slim

# Install only what's needed for shell commands (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Use Aliyun PyPI mirror to avoid SSL issues with pypi.org
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip config set global.trusted-host mirrors.aliyun.com

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user for defence-in-depth
RUN useradd -m -u 1001 runner

# Writable directory for runtime package installs (backed by named volume)
RUN mkdir -p /packages && chown runner:runner /packages

# Ensure runtime-installed packages are importable
ENV PYTHONPATH="/packages:/workspace"

USER runner

EXPOSE 8200

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200"]
