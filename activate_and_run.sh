source venv/bin/activate
python -m pip install -r requirements.txt

# Then run the pipeline
PYTHONPATH=src python -m stock_hunter.service
