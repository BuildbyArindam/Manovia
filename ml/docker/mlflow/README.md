# MLflow: local file store by default, this service optional (Day 31)

The platform's tracking URI order is: the config, then `MLFLOW_TRACKING_URI`,
then a local file store at `ml/mlruns` (`manovia_ml/paths.py`). That default is
deliberate — no server, no database, artefacts readable with `ls`, and a run
record that travels in the same repository as the code.

This compose file is for the two cases where a file store is the wrong answer:

- you want the **MLflow UI** against a store shared with someone else, or
- you want runs to survive a machine rebuild without copying a directory.

It uses **SQLite inside a named volume** rather than a file store because MLflow
3.x puts the filesystem backend in maintenance mode (the code sets
`MLFLOW_ALLOW_FILE_STORE=true` to keep the local default working; a server has no
reason to opt out).

```bash
make mlflow-up     # from the repository root, or: docker compose up -d here
# UI: http://localhost:5000
export MLFLOW_TRACKING_URI=http://localhost:5000
make ml-train CONFIG=configs/toy_emotion_sgd.yaml
make mlflow-down
```

Notes:

- The data lives in the `mlflow-data` volume; `docker compose down -v` deletes
  it. Nothing in this repository depends on the service existing.
- The same commands work against a remote server: point `MLFLOW_TRACKING_URI` at
  it and add credentials in your shell environment — **never** in a committed
  file (AGENTS.md rule: config via environment variables, no secrets in the repo).
- Startup depends on the `ghcr.io/mlflow/mlflow` image being pullable, so this
  file is a documented convenience rather than something CI exercises.
