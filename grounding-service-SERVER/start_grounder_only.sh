#!/bin/bash
set -e  # Exit on error

# Config
SERVICE_HOST="${GROUNDING_SERVICE_HOST:-127.0.0.1}"
SERVICE_PORT="${GROUNDING_SERVICE_PORT:-8000}"
ENV_NAME="${ENV_NAME:-grounder}"

echo "=========================================="
echo "Grounding Service Startup"
echo "=========================================="
echo "Host: $SERVICE_HOST"
echo "Port: $SERVICE_PORT"
echo "=========================================="

# Activate conda environment
eval "$(conda shell.bash hook)"
conda activate "$ENV_NAME"

# Verify Python environment
echo "Python: $(which python)"
echo "Python version: $(python --version)"

# Check if required packages are installed
echo "Checking dependencies..."
python -c "import torch; import transformers; import fastapi; print('Core dependencies OK')" || {
    echo "Missing dependencies. Please install:"
    echo "  pip install -r requirements.txt"
    exit 1
}

# Check GPU
echo "Checking GPU..."
python -c "import torch; print(f'GPU Available: {torch.cuda.is_available()}'); print(f'GPU Count: {torch.cuda.device_count()}'); print(f'GPU Name: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"

echo "=========================================="
echo "Starting grounder server..."
echo "=========================================="

export GROUNDING_SERVICE_HOST="$SERVICE_HOST"
export GROUNDING_SERVICE_PORT="$SERVICE_PORT"

python grounder_server.py
