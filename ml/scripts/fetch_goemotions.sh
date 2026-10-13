#!/usr/bin/env bash
#
# Fetch and convert GoEmotions for the first real training run (Day 31).
#
# This is the *only* script in ml/ that touches the network, and it is never run
# by CI or the test suite (AGENTS.md, ML ground rule 10). It exists because
# "download the corpus" was previously a paragraph in a README; a script is
# something the next person can run without re-reading the catalogue.
#
# Before running it, read ml/data/CATALOGUE.md § GoEmotions and accept the two
# conditions recorded there:
#   1. the licence is Apache-2.0, so the *code* is fine, but Reddit content is
#      not ours to redistribute: never commit the rows, never ship them in the
#      product, and treat them as English internet register rather than as
#      anything a person would type into a wellbeing companion;
#   2. GoEmotions is multi-label and has no `loneliness` class; the converter
#      keeps the first label in Manovia's priority order, which throws away
#      information. That loss is a known limitation, not a default.
#
# Usage:
#   ./scripts/fetch_goemotions.sh              # download train/validation/test
#   ./scripts/fetch_goemotions.sh --dvc-add    # ...then `dvc add` the directory
#   PYTHON=python3 ./scripts/fetch_goemotions.sh
#
# Requirements: the `datasets` package (Hugging Face) is NOT a dependency of this
# project — install it in this environment only:
#   pip install datasets
# and be aware that it is the package that performs the download.

set -euo pipefail

PYTHON="${PYTHON:-python}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ML_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${ML_ROOT}/data/raw/goemotions"

DVC_ADD=0
if [ "${1:-}" = "--dvc-add" ]; then
  DVC_ADD=1
elif [ -n "${1:-}" ]; then
  echo "unknown argument: ${1}" >&2
  echo "usage: $0 [--dvc-add]" >&2
  exit 2
fi

cd "${ML_ROOT}"

if ! "${PYTHON}" -c "import datasets" 2>/dev/null; then
  echo "error: the 'datasets' package is required for the download (pip install datasets)" >&2
  echo "       it is deliberately not a dependency of manovia-ml; see notebooks/README.md" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

for split in train validation test; do
  out="${OUT_DIR}/${split}.jsonl"
  if [ -f "${out}" ]; then
    echo "skip: ${out} already exists (delete it to re-download)"
    continue
  fi
  echo "fetching ${split} → ${out}"
  "${PYTHON}" -m manovia_ml.data.goemotions \
    --download "${split}" --split-name "${split}" --out "${out}"
done

echo
echo "converted files in ${OUT_DIR}:"
ls -1 "${OUT_DIR}"

if [ "${DVC_ADD}" -eq 1 ]; then
  echo
  echo "versioning the bytes with DVC (they stay out of Git):"
  "${PYTHON}" -m dvc add data/raw/goemotions
fi

cat <<'EOF'

Next (see ml/notebooks/README.md for the full sequence):

  1. copy the sha256 values this run logs into configs/colab_goemotions_logreg.yaml
     (`sha256:` and `rows:` under data.train / data.eval) — until then the config
     leaves them null on purpose;
  2. train:  python -m manovia_ml.training.train --config configs/colab_goemotions_logreg.yaml
  3. evaluate: python -m manovia_ml.evaluation.evaluate --run <RUN_ID>
  4. report:   python -m manovia_ml.reporting --run <RUN_ID>
  5. record the baseline: python -m manovia_ml.training.train \
       --config configs/colab_goemotions_logreg.yaml --record-expected

Bring back the report, the baseline and the run's JSON artefacts. Never the rows,
never model.joblib.
EOF
