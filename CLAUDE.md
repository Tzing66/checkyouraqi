# CheckYourAQI

The source of truth is [docs/checkyouraqi-plan.md](docs/checkyouraqi-plan.md). Read it first, then continue
from the first unchecked item in §12. Tick items off in the plan as they are done.

## Non-negotiables
- **Cost rule (§3):** before anything that touches AWS, say what it costs (or that it's free) and ask first.
  Region is `ap-south-1`. Tag everything `project=checkyouraqi`.
- No secrets in git. Local: `.env`. Cloud: SSM Parameter Store (SecureString).
- All timestamps stored in UTC; convert to IST only at display time.
- Decisions the plan doesn't cover: pick the simplest free option and log it in `docs/decisions.md`.

## Conventions
- Python 3.11+, `uv`, `ruff`, `pytest`, type hints. Small, testable modules; every extractor and transform gets a test.
- The project isn't installed as a package (`[tool.uv] package = false`). Run scripts with
  `PYTHONPATH=. uv run python ...`. Pytest already sets `pythonpath`.
- Docker Desktop doesn't start on its own: `open -a Docker` before `docker compose`.
