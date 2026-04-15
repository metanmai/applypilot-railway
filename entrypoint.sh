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

# Copy searches.yaml if it doesn't exist in /data (even if DB exists)
if [ ! -f "/data/searches.yaml" ]; then
    if [ -f "/seed-data/searches.yaml" ]; then
        echo "Copying searches.yaml from seed-data..."
        cp /seed-data/searches.yaml /data/searches.yaml
    elif [ -f "/seed-config/default-searches.yaml" ]; then
        echo "Copying default searches.yaml..."
        cp /seed-config/default-searches.yaml /data/searches.yaml
    fi
fi

# Link ApplyPilot config to data directory
if [ ! -L "/root/.applypilot" ]; then
    rm -rf /root/.applypilot
    ln -s /data /root/.applypilot
fi

# Run the main command
exec "$@"
