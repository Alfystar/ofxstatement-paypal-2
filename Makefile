all: test mypy black ruff

.PHONY: test
test:
	python3 -m unittest discover -s tests

.PHONY: coverage
coverage:
	pytest --cov src/ofxstatement

.PHONY: black
black:
	black src tests

.PHONY: mypy
mypy:
	mypy src tests

.PHONY: ruff
ruff:
	ruff check src tests
