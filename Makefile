.PHONY: dev install test eval seed demo lint stop

# ── Development ───────────────────────────────────────────────────────────────
dev:
	bash start_all.sh

# ── Install ───────────────────────────────────────────────────────────────────
install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	@if [ -d apps/web ]; then \
		echo "Installing Node.js dependencies..."; \
		cd apps/web && npm install; \
	fi

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	PYTHONPATH=apps/api:. .venv/bin/pytest tests/ -q

# ── Evals ─────────────────────────────────────────────────────────────────────
eval:
	PYTHONPATH=apps/api:. .venv/bin/python3 -m evals.runners.run_demo_eval

# ── Seed dataset ──────────────────────────────────────────────────────────────
seed:
	@if [ -f scripts/process_kaggle_dataset.py ]; then \
		PYTHONPATH=apps/api:. .venv/bin/python3 scripts/process_kaggle_dataset.py; \
	else \
		echo "scripts/process_kaggle_dataset.py not found — skipping seed"; \
	fi

# ── Demo ──────────────────────────────────────────────────────────────────────
demo:
	@echo "Opening GigBid UI at http://localhost:3000"
	@xdg-open http://localhost:3000 2>/dev/null || open http://localhost:3000 2>/dev/null || \
		echo "Visit http://localhost:3000 in your browser"

# ── Lint ──────────────────────────────────────────────────────────────────────
lint:
	.venv/bin/ruff check . --fix

# ── Stop ──────────────────────────────────────────────────────────────────────
stop:
	@echo "Killing services on 8000, 8002, 3000..."
	@fuser -k 8000/tcp 2>/dev/null || true
	@fuser -k 8002/tcp 2>/dev/null || true
	@fuser -k 3000/tcp 2>/dev/null || true
	@pkill -f "next-server" 2>/dev/null || true
	@pkill -f "next dev"    2>/dev/null || true
	@pkill -f "uvicorn"     2>/dev/null || true
	@pkill -f "mcp_servers.marketplace" 2>/dev/null || true
	@echo "Done."
