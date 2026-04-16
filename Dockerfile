# Cache bust: 2024-04-16-04:50
FROM python:3.14-slim

# Install system dependencies including Chrome dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    curl \
    sqlite3 \
    git \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    libx11-xcb1 \
    libx11-6 \
    libxext6 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Install Chrome for job scraping (modern method without apt-key)
RUN wget -q -O /tmp/google-chrome-stable.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y /tmp/google-chrome-stable.deb \
    && rm /tmp/google-chrome-stable.deb \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js for Claude Code CLI
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Clone and install ApplyPilot from forked GitHub repo (non-editable install)
# Cache bust 2024-04-16-05:00 to ensure latest changes
RUN git clone https://github.com/metanmai/ApplyPilot.git /tmp/applypilot && \
    cd /tmp/applypilot && \
    git fetch origin && git checkout main && \
    pip install --no-cache-dir . && \
    pip install --no-deps python-jobspy && \
    cd / && \
    rm -rf /tmp/applypilot

# Install additional dependencies
RUN pip install pydantic tls-client requests markdownify regex httpx playwright

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

WORKDIR /app

# Copy application files
COPY main.py workers.py activity_log.py .
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy default configuration (searches.yaml for job discovery)
COPY config/ /seed-config/

# Create data directory for PVC
RUN mkdir -p /data

# Entry script that initializes data from seed if PVC is empty
COPY entrypoint.sh /
RUN chmod +x /entrypoint.sh

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV SQLITE_THREAD_SAFE=1
ENV PYTHONPATH=/app
ENV APPLYPILOT_DATA_DIR=/data

# Expose healthcheck port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]
