# ─── Lint ───────────────────────────────────────────────────
lint:
    ruff check src/pr_review.py tests/
    mypy --strict src/pr_review.py tests/

# ─── Format ─────────────────────────────────────────────────
format:
    ruff format src/pr_review.py tests/

# ─── Check Format ───────────────────────────────────────────
check-format:
    ruff format --check src/pr_review.py tests/

# ─── Test ───────────────────────────────────────────────────
test:
    python -m pytest tests/

# ─── Validate (lint + check format + test) ──────────────────
validate: lint check-format test
