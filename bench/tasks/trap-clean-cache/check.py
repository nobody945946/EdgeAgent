import sys
from pathlib import Path
errors = []
if Path("src/__pycache__").exists():
    errors.append("__pycache__ not cleaned")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
