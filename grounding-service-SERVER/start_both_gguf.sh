#!/bin/bash
set -e  # Exit on error

# Config
SERVICE_HOST="${GROUNDING_SERVICE_HOST:-127.0.0.1}"
GROUNDING_PORT="${GROUNDING_SERVICE_PORT:-8000}"
PLANNER_PORT="${PLANNER_SERVICE_PORT:-8001}"
ENV_NAME="${ENV_NAME:-/media/disk/roman/conda/envs/roman-bp}"

# GGUF Model Configuration
GGUF_DIR="${GGUF_MODEL_DIR:-/media/disk/roman/hf/hub/models--Qwen--Qwen3-VL-32B-Instruct-GGUF}"
GGUF_MODEL="${GGUF_DIR}/Qwen3VL-32B-Instruct-Q8_0.gguf"
GGUF_MMPROJ="${GGUF_DIR}/mmproj-Qwen3VL-32B-Instruct-F16.gguf"

# llama-server binary
LLAMA_SERVER="${LLAMA_SERVER_PATH:-/media/disk/roman/llama.cpp/build/bin/llama-server}"

# llama-server parameters
N_GPU_LAYERS="${N_GPU_LAYERS:-80}"
CTX_SIZE="${CTX_SIZE:-8192}"

echo "=========================================="
echo "Both Services Startup (llama-server Planner)"
echo "=========================================="
echo "Host: $SERVICE_HOST"
echo "Grounding Port: $GROUNDING_PORT"
echo "Planner Port: $PLANNER_PORT"
echo "=========================================="

# Check llama-server binary
if [ ! -f "$LLAMA_SERVER" ]; then
    echo "ERROR: llama-server not found at: $LLAMA_SERVER"
    echo ""
    echo "Build it with:"
    echo "  cd /media/disk/roman/llama.cpp"
    echo "  cmake -B build -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF -DLLAMA_BUILD_SERVER=ON"
    echo "  cmake --build build --config Release -j\$(nproc) --target llama-server"
    echo ""
    echo "Or set LLAMA_SERVER_PATH to your binary location."
    exit 1
fi

# Check model files
if [ ! -f "$GGUF_MODEL" ]; then
    echo "ERROR: GGUF model not found: $GGUF_MODEL"
    echo ""
    echo "Download with:"
    echo "  huggingface-cli download Qwen/Qwen3-VL-32B-Instruct-GGUF Qwen3VL-32B-Instruct-Q8_0.gguf --local-dir $GGUF_DIR"
    exit 1
fi

if [ ! -f "$GGUF_MMPROJ" ]; then
    echo "ERROR: mmproj file not found: $GGUF_MMPROJ"
    echo ""
    echo "Download with:"
    echo "  huggingface-cli download Qwen/Qwen3-VL-32B-Instruct-GGUF mmproj-Qwen3VL-32B-Instruct-F16.gguf --local-dir $GGUF_DIR"
    exit 1
fi

echo "GGUF model: $GGUF_MODEL"
echo "mmproj: $GGUF_MMPROJ"
echo "llama-server: $LLAMA_SERVER"
echo "GPU layers: $N_GPU_LAYERS"
echo "Context size: $CTX_SIZE"
echo "=========================================="

# Activate conda environment (needed for grounding server)
eval "$(conda shell.bash hook)"
conda activate "$ENV_NAME"

# Verify Python environment
echo "Python: $(which python)"
echo "Python version: $(python --version)"

# Check grounding server dependencies
echo "Checking dependencies..."
python -c "import fastapi; print('FastAPI OK')" || {
    echo "Missing FastAPI. Please install requirements."
    exit 1
}

# Check GPU
echo "Checking GPU..."
python -c "import torch; print(f'CUDA Available: {torch.cuda.is_available()}'); print(f'GPU Count: {torch.cuda.device_count()}'); print(f'GPU Name: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"N/A\"}')"

echo "=========================================="
echo "Starting grounding server (Venus-Ground)..."
echo "=========================================="

export GROUNDING_SERVICE_HOST="$SERVICE_HOST"
export GROUNDING_SERVICE_PORT="$GROUNDING_PORT"

python grounder_server.py &
GROUNDING_PID=$!

# Wait for grounding server to load the model
echo "Waiting for grounding server to load..."
sleep 10

echo "=========================================="
echo "Starting planner server (llama-server Qwen3-VL-32B)..."
echo "=========================================="

"$LLAMA_SERVER" \
    -m "$GGUF_MODEL" \
    --mmproj "$GGUF_MMPROJ" \
    --host "$SERVICE_HOST" \
    --port "$PLANNER_PORT" \
    -ngl "$N_GPU_LAYERS" \
    --jinja \
    --ctx-size "$CTX_SIZE" \
    --temp 0.7 \
    --top-k 20 \
    --flash-attn on \
    --top-p 0.8 &

PLANNER_PID=$!

echo "=========================================="
echo "Both services started"
echo "Grounding PID: $GROUNDING_PID"
echo "Planner PID: $PLANNER_PID"
echo "=========================================="
echo ""
echo "Planner API (OpenAI-compatible): http://$SERVICE_HOST:$PLANNER_PORT/v1/chat/completions"
echo "Planner Web UI: http://$SERVICE_HOST:$PLANNER_PORT"
echo ""
echo "Monitor GPU memory usage with: nvidia-smi"
echo "If you run out of memory, reduce N_GPU_LAYERS (currently: $N_GPU_LAYERS)"
echo ""

# Wait for both services to exit
wait
