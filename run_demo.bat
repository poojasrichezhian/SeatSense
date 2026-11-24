@echo off
REM One-command demo on synthetic data (Windows)
cd /d %~dp0
python -m pip install -q -r requirements.txt
python scripts\make_sample_data.py --out data/sample --days 14
python scripts\build_model.py --config config.sample.yaml
python scripts\validate.py --config config.sample.yaml > NUL
echo Open http://127.0.0.1:8000  (Ctrl+C to stop)
set SEATSENSE_CONFIG=config.sample.yaml
python -m uvicorn seatsense.api:app --port 8000
