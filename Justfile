# ─── Lint ───────────────────────────────────────────────────
lint:
    ruff check pr_review.py tests/
    mypy --strict pr_review.py tests/

# ─── Format ─────────────────────────────────────────────────
format:
    ruff format pr_review.py tests/

# ─── Check Format ───────────────────────────────────────────
check-format:
    ruff format --check pr_review.py tests/

# ─── Test ───────────────────────────────────────────────────
test:
    python -m pytest tests/

# ─── Validate (lint + check format + test) ──────────────────
validate: lint check-format test
