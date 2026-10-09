.PHONY: dev test lint format eval up down

# Backend targets are wired to real commands (Day 2). Frontend targets will be
# added when the frontend scaffold exists; eval/up/down remain placeholders.

dev:
	$(MAKE) -C backend dev

test:
	$(MAKE) -C backend test

lint:
	$(MAKE) -C backend lint

format:
	$(MAKE) -C backend format

eval up down:
	@echo "not implemented yet"
