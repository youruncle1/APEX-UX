#!/bin/bash
set -e

ENV_NAME="${ENV_NAME:-grounder}"
PYTHON_VERSION="${PYTHON_VERSION:-3.10}"

eval "$(conda shell.bash hook)"

if ! conda env list | grep -q "^$ENV_NAME "; then
    conda create -y -n "$ENV_NAME" python="$PYTHON_VERSION"
fi

conda activate "$ENV_NAME"

pip install --upgrade pip
pip install -r requirements.txt
