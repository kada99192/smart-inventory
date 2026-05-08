#!/usr/bin/env bash
set -e

ENV_NAME="smart-inventory"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v conda >/dev/null 2>&1; then
    echo "[ERROR] conda not found in PATH. Please install Miniconda/Anaconda first."
    exit 1
fi

CONDA_BASE="$(conda info --base)"
# shellcheck disable=SC1091
source "$CONDA_BASE/etc/profile.d/conda.sh"

if ! conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
    echo "[INFO] Creating conda environment \"$ENV_NAME\" from environment.yml ..."
    conda env create -f "$SCRIPT_DIR/environment.yml"
else
    echo "[INFO] Conda environment \"$ENV_NAME\" already exists."
fi

conda activate "$ENV_NAME"

echo "[INFO] Launching Streamlit app ..."
streamlit run "$SCRIPT_DIR/src/app.py"
