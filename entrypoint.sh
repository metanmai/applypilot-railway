#!/bin/bash
set -e

# Initialize PVC with seed data if empty
if [ ! -f "/data/applypilot.db" ] && [ -f "/seed-data/applypilot.db" ]; then
    echo "Initializing PVC with seed data..."
    cp /seed-data/*.db /seed-data/*.json /seed-data/*.yaml /seed-data/*.txt /seed-data/.env /data/ 2>/dev/null || true
    mkdir -p /data/logs /data/tailored_resumes /data/cover_letters /data/apply-workers /data/chrome-workers
    echo "PVC initialized with seed data"
fi

# Ensure directories exist
mkdir -p /data/logs /data/tailored_resumes /data/cover_letters /data/apply-workers /data/chrome-workers

# Link ApplyPilot config to data directory
if [ ! -L "/root/.applypilot" ]; then
    rm -rf /root/.applypilot
    ln -s /data /root/.applypilot
fi

# Run the main command
exec "$@"
