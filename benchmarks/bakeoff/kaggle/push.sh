#!/usr/bin/env bash
# Launch the Kaggle GPU leg of the layout-region spike (roadmap 14 item 3.7b).
#
#   KAGGLE_USERNAME=<user> benchmarks/bakeoff/kaggle/push.sh          # upload data, push and run the kernel
#   KAGGLE_USERNAME=<user> benchmarks/bakeoff/kaggle/push.sh fetch    # after it finishes: download outputs
#
# Needs the `kaggle` CLI (pip install kaggle) and Kaggle API credentials
# (~/.kaggle/kaggle.json, KAGGLE_KEY, or a proxy that injects them). Uploads
# the two harness scripts and the test PDFs (public PBR journals and patent
# specifications) as a PRIVATE dataset, and runs kernel.py as a PRIVATE
# script kernel on one T4 with internet on (pip + the Hugging Face model).
# Set RUN_VLLM=1 to also try the vLLM route. Free GPU quota is used; no paid
# API is called.
set -euo pipefail
: "${KAGGLE_USERNAME:?set KAGGLE_USERNAME}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
STAGE="${STAGE:-$(mktemp -d)}"
DATASET="$KAGGLE_USERNAME/wiseau-layout-spike"
KERNEL="$KAGGLE_USERNAME/wiseau-layout-spike-run"

if [[ "${1:-}" == "fetch" ]]; then
  kaggle kernels status "$KERNEL"
  dest="$ROOT/benchmarks/bakeoff/outputs/kaggle-$(date +%Y%m%d)"
  mkdir -p "$dest"
  kaggle kernels output "$KERNEL" -p "$dest"
  echo "outputs in $dest (unzip outputs.zip; commit them right away)"
  exit 0
fi

mkdir -p "$STAGE/data" "$STAGE/kernel" "$STAGE/tree/wiseau/benchmarks/bakeoff/kaggle" "$STAGE/tree/wiseau/pdf-test"
cp "$ROOT/benchmarks/bakeoff/layout_models.py" "$ROOT/benchmarks/bakeoff/regions.py" \
   "$STAGE/tree/wiseau/benchmarks/bakeoff/"
cp "$HERE/vllm_smoke.py" "$STAGE/tree/wiseau/benchmarks/bakeoff/kaggle/"
cp -r "$ROOT/pdf-test/." "$STAGE/tree/wiseau/pdf-test/"
if [[ "${RUN_VLLM:-0}" == "1" ]]; then touch "$STAGE/tree/wiseau/RUN_VLLM"; fi  # kernel.py checks for it
tar -C "$STAGE/tree" -cf "$STAGE/data/spike.tar" wiseau
cat > "$STAGE/data/dataset-metadata.json" <<EOF
{"title": "wiseau-layout-spike", "id": "$DATASET", "licenses": [{"name": "other"}]}
EOF
if kaggle datasets status "$DATASET" >/dev/null 2>&1; then
  kaggle datasets version -p "$STAGE/data" -m "spike inputs $(git -C "$ROOT" rev-parse --short HEAD)"
else
  kaggle datasets create -p "$STAGE/data"   # private by default
fi

cp "$HERE/kernel.py" "$STAGE/kernel/kernel.py"
cat > "$STAGE/kernel/kernel-metadata.json" <<EOF
{"id": "$KERNEL", "title": "wiseau-layout-spike-run", "code_file": "kernel.py",
 "language": "python", "kernel_type": "script", "is_private": true,
 "enable_gpu": true, "enable_internet": true, "machine_shape": "NvidiaTeslaT4",
 "dataset_sources": ["$DATASET"], "competition_sources": [], "kernel_sources": []}
EOF
kaggle kernels push -p "$STAGE/kernel"
echo "pushed $KERNEL; check with: kaggle kernels status $KERNEL; then: $0 fetch"
