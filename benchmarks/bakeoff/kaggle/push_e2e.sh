#!/usr/bin/env bash
# Launch the end-to-end GPU benchmark (e2e_kernel.py) on a Kaggle T4.
#
#   KAGGLE_USERNAME=<user> benchmarks/bakeoff/kaggle/push_e2e.sh          # upload data, push and run the kernel
#   KAGGLE_USERNAME=<user> benchmarks/bakeoff/kaggle/push_e2e.sh kernel   # re-push the kernel only (data unchanged)
#   KAGGLE_USERNAME=<user> benchmarks/bakeoff/kaggle/push_e2e.sh fetch    # after it finishes: download outputs
#
# Uploads the backend, glm-ocr/, the harness (regions.py + kaggle/) and the test
# PDFs (public PBR journals and patent specifications) as a PRIVATE dataset, and
# runs e2e_kernel.py as a PRIVATE script kernel on one T4 with internet on (pip,
# apt, and the Hugging Face models). The vLLM leg runs unless RUN_VLLM=0. Free GPU
# quota is used (several hours: the CPU legs are the long ones); no paid API.
set -euo pipefail
: "${KAGGLE_USERNAME:?set KAGGLE_USERNAME}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
STAGE="${STAGE:-$(mktemp -d)}"
DATASET="$KAGGLE_USERNAME/wiseau-gpu-e2e"
KERNEL="$KAGGLE_USERNAME/wiseau-gpu-e2e-run"

if [[ "${1:-}" == "fetch" ]]; then
  kaggle kernels status "$KERNEL"
  dest="$ROOT/benchmarks/bakeoff/outputs/kaggle-e2e-$(date +%Y%m%d)"
  mkdir -p "$dest"
  kaggle kernels output "$KERNEL" -p "$dest"
  echo "outputs in $dest (unzip outputs.zip; commit them right away)"
  exit 0
fi

if [[ "${1:-}" != "kernel" ]]; then
T="$STAGE/tree/wiseau"
mkdir -p "$STAGE/data" "$STAGE/kernel" "$T/benchmarks/bakeoff/kaggle" "$T/pdf-test" "$T/backend" "$T/glm-ocr"
tar -C "$ROOT/backend" --exclude .venv --exclude __pycache__ --exclude tests -cf - . | tar -C "$T/backend" -xf -
cp "$ROOT"/glm-ocr/*.py "$ROOT/glm-ocr/requirements.txt" "$T/glm-ocr/"
cp "$ROOT/benchmarks/bakeoff/regions.py" "$T/benchmarks/bakeoff/"
cp "$HERE/e2e.py" "$HERE/gpu_device.py" "$T/benchmarks/bakeoff/kaggle/"
cp -r "$ROOT/pdf-test/." "$T/pdf-test/"
if [[ "${RUN_VLLM:-1}" == "1" ]]; then touch "$T/RUN_VLLM"; fi   # e2e_kernel.py checks for it
# LEGS="docling vllm" runs only those leg groups (image, docling, glm, vllm); unset runs all.
if [[ -n "${LEGS:-}" ]]; then echo "$LEGS" > "$T/LEGS"; fi
tar -C "$STAGE/tree" -cf "$STAGE/data/e2e.tar" wiseau
cat > "$STAGE/data/dataset-metadata.json" <<EOF
{"title": "wiseau-gpu-e2e", "id": "$DATASET", "licenses": [{"name": "other"}]}
EOF
if kaggle datasets status "$DATASET" >/dev/null 2>&1; then
  kaggle datasets version -p "$STAGE/data" -m "e2e inputs $(git -C "$ROOT" rev-parse --short HEAD)"
else
  kaggle datasets create -p "$STAGE/data"   # private by default
fi
fi
# A kernel pushed while its dataset is still processing starts without it.
for _ in $(seq 1 60); do
  kaggle datasets status "$DATASET" 2>/dev/null | grep -qi ready && break
  sleep 10
done

mkdir -p "$STAGE/kernel"
sed "s/^LEGS = \"\"/LEGS = \"${LEGS:-}\"/" "$HERE/e2e_kernel.py" > "$STAGE/kernel/e2e_kernel.py"
cat > "$STAGE/kernel/kernel-metadata.json" <<EOF
{"id": "$KERNEL", "title": "wiseau-gpu-e2e-run", "code_file": "e2e_kernel.py",
 "language": "python", "kernel_type": "script", "is_private": true,
 "enable_gpu": true, "enable_internet": true, "machine_shape": "NvidiaTeslaT4",
 "dataset_sources": ["$DATASET"], "competition_sources": [], "kernel_sources": []}
EOF
kaggle kernels push -p "$STAGE/kernel"
echo "pushed $KERNEL; check with: kaggle kernels status $KERNEL; then: $0 fetch"
