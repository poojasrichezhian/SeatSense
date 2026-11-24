#!/usr/bin/env bash
# One-command demo on synthetic data. Usage: ./run_demo.sh
set -e
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
python3 scripts/make_sample_data.py --out data/sample --days 14
python3 scripts/build_model.py --config config.sample.yaml
python3 scripts/validate.py --config config.sample.yaml > /dev/null
echo "Open http://127.0.0.1:8000  (Ctrl+C to stop)"
SEATSENSE_CONFIG=config.sample.yaml python3 -m uvicorn seatsense.api:app --port 8000
