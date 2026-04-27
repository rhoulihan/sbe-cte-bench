# Pull Request

## What this changes

<!-- One-paragraph summary. Link the spec section in `docs/` that motivated the change. -->

## TDD evidence

<!-- For new code: list the failing-then-passing commit pair, OR explain why a
single combined commit was appropriate. Quality gates expect tests to motivate
implementation, not the other way around. -->

- Failing test commit: `<sha>`
- Passing implementation commit: `<sha>`

## Spec linkage

<!-- Which sections of `docs/` does this PR touch or depend on? -->

- [ ] No spec change required
- [ ] Spec was updated (cite section): _________

## Quality gate self-check

- [ ] `uv run ruff check src/ tests/` clean
- [ ] `uv run ruff format --check src/ tests/` clean
- [ ] `uv run mypy src/ tests/` clean
- [ ] `uv run pytest -m "unit or property or golden"` green
- [ ] Coverage hasn't regressed (per-module thresholds in `pyproject.toml`)
- [ ] `pre-commit run --all-files` clean
- [ ] If goldens changed, justification included below

## Goldens delta

<!-- If `tests/golden/*` changed, paste the diff (or screenshot for SVG)
and explain why. A reviewer must explicitly approve goldens updates. -->

N/A
