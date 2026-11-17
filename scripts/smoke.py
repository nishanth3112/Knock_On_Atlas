"""Post-deploy smoke test: the API is up and both URLs refuse anonymous callers.

Usage: uv run python scripts/smoke.py <api function URL> <mcp URL>
"""

import sys

import httpx


def problems(http: httpx.Client, api: str, mcp: str) -> list[str]:
    found = []
    health = http.get(f"{api}/health")
    if health.status_code != 200 or health.json().get("ok") is not True:
        found.append(f"/health answered {health.status_code}: {health.text[:200]}")
    if (code := http.get(f"{api}/analyses").status_code) != 401:
        found.append(f"/analyses without a token answered {code}, not 401")
    listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    accept = {"accept": "application/json, text/event-stream"}
    if (code := http.post(mcp, json=listing, headers=accept).status_code) != 401:
        found.append(f"MCP without a token answered {code}, not 401")
    return found


if __name__ == "__main__":
    api_url, mcp_url = sys.argv[1].rstrip("/"), sys.argv[2]
    # Cold starts import the whole app (several seconds); allow for them.
    with httpx.Client(timeout=60) as client:
        failed = problems(client, api_url, mcp_url)
    for problem in failed:
        print(f"FAIL {problem}")
    print("smoke test passed" if not failed else f"{len(failed)} smoke checks failed")
    sys.exit(1 if failed else 0)
