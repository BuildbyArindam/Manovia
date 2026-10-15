"""Colab/Kaggle entry point. Install the ML project first; run from ml/.

Exact install, five independent seeds and artifact collection: ml/README.md,
section Day 38. This script is synthetic-only and never saves weights/data.
"""

from manovia_ml.federated.experiment import main

if __name__ == "__main__":
    main()
