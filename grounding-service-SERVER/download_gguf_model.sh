#!/bin/bash
set -e

# GGUF Model Download Helper Script
# Downloads Qwen3-VL-32B-Instruct GGUF model and sets up environment

echo "=========================================="
echo "GGUF Model Download Script"
echo "=========================================="
echo ""

# Check if huggingface-cli is installed
if ! command -v huggingface-cli &> /dev/null; then
    echo "huggingface-cli not found. Installing..."
    pip install huggingface-hub
fi

# Default download location - use /media/disk/roman/hf if available
# This follows HuggingFace's standard structure and keeps all models together
if [ -d "/media/disk/roman/hf" ]; then
    DEFAULT_MODEL_DIR="/media/disk/roman/hf/hub/models--Qwen--Qwen3-VL-32B-Instruct-GGUF"
    echo "Using HuggingFace cache directory (same as your other models)"
else
    DEFAULT_MODEL_DIR="$HOME/models/qwen3-vl-32b-gguf"
fi

MODEL_DIR="${1:-$DEFAULT_MODEL_DIR}"

echo "Download location: $MODEL_DIR"
if [ -d "/media/disk/roman/hf" ]; then
    echo "(Storing in /media/disk/roman/hf alongside your existing models)"
fi
echo ""

# Ask for quantization level
echo "Available quantization levels:"
echo "  1) Q8_0 (~34GB) - Recommended - Best quality (99.5%)"
echo "  2) Q6_K (~24GB) - Good balance (98%)"
echo "  3) Q5_K_M (~20GB) - VRAM constrained (95%)"
echo "  4) Q4_K_M (~16GB) - Very limited VRAM (90%)"
echo ""
read -p "Select quantization level [1-4, default=1]: " quant_choice

case $quant_choice in
    2)
        MODEL_FILE="Qwen3VL-32B-Instruct-Q6_K.gguf"
        echo "Selected: Q6_K (~24GB)"
        ;;
    3)
        MODEL_FILE="Qwen3VL-32B-Instruct-Q5_K_M.gguf"
        echo "Selected: Q5_K_M (~20GB)"
        ;;
    4)
        MODEL_FILE="Qwen3VL-32B-Instruct-Q4_K_M.gguf"
        echo "Selected: Q4_K_M (~16GB)"
        ;;
    *)
        MODEL_FILE="Qwen3VL-32B-Instruct-Q8_0.gguf"
        echo "Selected: Q8_0 (~34GB) [Recommended]"
        ;;
esac

echo ""
echo "Downloading $MODEL_FILE to $MODEL_DIR"
echo "This will take a while depending on your internet speed..."
echo ""

# Create directory
mkdir -p "$MODEL_DIR"

# Download model
huggingface-cli download Qwen/Qwen3-VL-32B-Instruct-GGUF \
    "$MODEL_FILE" \
    --local-dir "$MODEL_DIR" \
    --resume-download

MODEL_PATH="$MODEL_DIR/$MODEL_FILE"

echo ""
echo "=========================================="
echo "✓ Download complete!"
echo "=========================================="
echo ""
echo "Model saved to: $MODEL_PATH"
echo ""
echo "Next steps:"
echo ""
echo "1. Set environment variable:"
echo "   export PLANNER_MODEL_PATH=$MODEL_PATH"
echo ""
echo "2. Add to your shell profile for persistence:"
echo "   echo 'export PLANNER_MODEL_PATH=$MODEL_PATH' >> ~/.bashrc"
echo "   source ~/.bashrc"
echo ""
echo "3. Start the services:"
echo "   cd grounding-service-SERVER"
echo "   ./start_both_gguf.sh"
echo ""
echo "=========================================="

# Offer to add to bashrc
read -p "Add PLANNER_MODEL_PATH to ~/.bashrc now? [y/N]: " add_to_bashrc

if [[ $add_to_bashrc =~ ^[Yy]$ ]]; then
    echo "export PLANNER_MODEL_PATH=$MODEL_PATH" >> ~/.bashrc
    echo "✓ Added to ~/.bashrc"
    echo "Run: source ~/.bashrc"
else
    echo "Skipped. Remember to set PLANNER_MODEL_PATH manually."
fi

echo ""
echo "Done!"