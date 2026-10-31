setup:
	pip install -r requirements.txt

model:
	bash scripts/fetch_model.sh

check:
	python3 scripts/check_env.py

web:
	python3 web/app.py

rps:
	python3 games/rps.py --rounds 5

mimic:
	python3 games/mimic.py --seconds 30

probe:
	python3 lessons/l1_probe.py

.PHONY: setup model check web rps mimic probe
