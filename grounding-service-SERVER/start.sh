#!/bin/bash

# Grounding Service Startup Script
# 
# Usage:
#   ./start.sh                    # Start on localhost:8000
#   GROUNDING_SERVICE_PORT=9000 ./start.sh   # Custom port
#   GROUNDING_SERVICE_HOST=0.0.0.0 ./start.sh  # Public access (not recommended)

set -e  # Exit on error

# Configuration
SERVICE_HOST="${GROUNDING_SERVICE_HOST:-127.0.0.1}"
SERVICE_PORT="${GROUNDING_SERVICE_PORT:-8000}"
ROMAN_BASE="${ROMAN_BASE:-/media/disk/roman}"

echo "=========================================="
echo "Grounding Service Startup"
echo "=========================================="
echo "Host: $SERVICE_HOST"
echo "Port: $SERVICE_PORT"
echo "=========================================="

# Source environment variables
echo "Loading environment variables..."
source "$ROMAN_BASE/roman-bp.sh"

# Activate conda environment
echo "Activating conda environment..."
eval "$(conda shell.bash hook)"
conda activate "$ROMAN_BASE/conda/envs/roman-bp"

# Verify Python environment
echo "Python: $(which python)"
echo "Python version: $(python --version)"

# Check if required packages are installed
echo "Checking dependencies..."
python -c "import torch; import transformers; import fastapi; print('✓ Core dependencies OK')" || {
    echo "✗ Missing dependencies. Please install:"
    echo "  pip install -r requirements.txt"
    exit 1
}

# Check GPU
echo "Checking GPU..."
python -c "import torch; print(f'GPU Available: {torch.cuda.is_available()}'); print(f'GPU Count: {torch.cuda.device_count()}'); print(f'GPU Name: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"

echo "=========================================="
echo "Starting server..."
echo "=========================================="

# Start server
export GROUNDING_SERVICE_HOST="$SERVICE_HOST"
export GROUNDING_SERVICE_PORT="$SERVICE_PORT"

python server.py

# Verify Python environment
echo "Python: $(which python)"
echo "Python version: $(python --version)"

# Check if required packages are installed
echo "Checking dependencies..."
python -c "import torch; import transformers; import fastapi; print('✓ Core dependencies OK')"

# Check GPU
echo "Checking GPU..."
python -c "import torch; print(f'GPU Available: {torch.cuda.is_available()}'); print(f'GPU Count: {torch.cuda.device_count()}'); print(f'GPU Name: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"

echo "=========================================="
echo "Starting server..."
echo "=========================================="

# Start server
export GROUNDING_SERVICE_HOST="$SERVICE_HOST"
export GROUNDING_SERVICE_PORT="$SERVICE_PORT"

python server.py
