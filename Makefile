# Demo switch and smoke test (aws-guide section 11). Run `aws login` first.
# Live runs stay on until the time in /koa/prod/DEMO_LIVE_UNTIL, so forgetting
# demo-off is safe; the API, worker and poller notice within a minute.

PARAM := /koa/prod/DEMO_LIVE_UNTIL
TF := infra/terraform
API_URL ?= $(shell terraform -chdir=$(TF) output -raw api_url 2>/dev/null)
MCP_URL ?= $(shell terraform -chdir=$(TF) output -raw mcp_url 2>/dev/null)

.PHONY: demo-on demo-off smoke

demo-on:
	aws ssm put-parameter --name $(PARAM) --type String --overwrite \
		--value "$$(uv run python -c 'from datetime import UTC, datetime, timedelta; print((datetime.now(UTC) + timedelta(hours=3)).isoformat(timespec="seconds"))')"
	curl -fsS "$(API_URL)health" && echo  # wakes the API before the demo

demo-off:
	aws ssm put-parameter --name $(PARAM) --type String --overwrite \
		--value 2000-01-01T00:00:00+00:00

smoke:
	uv run python scripts/smoke.py "$(API_URL)" "$(MCP_URL)"
