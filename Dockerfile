FROM python:3.14-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    curl \
    sqlite3 \
    git \
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
RUN git clone https://github.com/metanmai/ApplyPilot.git /tmp/applypilot && \
    cd /tmp/applypilot && \
    pip install --no-cache-dir . && \
    pip install --no-deps python-jobspy && \
    cd / && \
    rm -rf /tmp/applypilot

# Install additional dependencies
RUN pip install pydantic tls-client requests markdownify regex httpx

WORKDIR /app

# Copy application files
COPY main.py workers.py .
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy seed data (will be used to initialize PVC if empty)
# Note: seed-data is in .gitignore for security, so we create an empty directory
RUN mkdir -p /seed-data

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
