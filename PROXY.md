# Proxy Auto Policy

Proxy mode is fail-open by default so offline analysis and reverse engineering are never blocked:

- Proxy reachable: record `proxy.checked`; all target traffic must use it.
- Proxy connection unavailable: record `proxy.unavailable`, print a warning, and continue in
  `direct` mode.
- Proxy accepts the connection but rejects CONNECT: record `proxy.rejected` and stop. A reachable
  proxy must not be bypassed.

The default endpoint is `${PENTEST_PROXY:-http://127.0.0.1:8080}`.

## Startup

```bash
. .pi/pentest/engagement_env.sh
JOIN=$(python3 .pi/pentest/swarm.py join --label "$LABEL" \
  --proxy-policy "${PENTEST_PROXY_POLICY:-auto}")
# Extract AGENT from JOIN, then:
. .pi/pentest/proxy_env.sh --agent "$AGENT"
python3 .pi/pentest/swarm.py proxy-check --agent "$AGENT" \
  --proxy "$PENTEST_PROXY" --timeout 5
. .pi/pentest/proxy_env.sh --agent "$AGENT"
```

`next` waits for a trusted decision: `proxy.checked` and auto-policy `proxy.unavailable` allow work;
latest `proxy.rejected` blocks it. Generic `emit` cannot forge these events. Source `engagement_env.sh` and `proxy_env.sh --agent "$AGENT"` in every new shell;
the latter restores `PENTEST_NETWORK_MODE` from the ledger and configures or unsets proxy variables.

Strict fail-closed behavior remains available:

```bash
PENTEST_PROXY_POLICY=required pi
# or join --proxy-policy required
```

`--proxy-policy off` skips preflight entirely and is intended only for explicitly offline workflows.

## Tool settings

Always branch on `PENTEST_NETWORK_MODE`. Use finite timeouts in both modes.

### curl

```bash
. .pi/pentest/engagement_env.sh
. .pi/pentest/proxy_env.sh --agent "$AGENT"
if [ "$PENTEST_NETWORK_MODE" = proxy ]; then
  curl --proxy "$PENTEST_PROXY" \
    --proxy-header "X-Redteam-Agent: $AGENT" \
    --proxy-header "X-Redteam-Engagement: $PENTEST_ENGAGEMENT_ID" \
    --connect-timeout 10 --max-time 30 "$URL"
else
  curl --connect-timeout 10 --max-time 30 "$URL"
fi
```

### Python requests

```python
import os, requests
kwargs = {"timeout": 30}
if os.environ["PENTEST_NETWORK_MODE"] == "proxy":
    proxy = os.environ["PENTEST_PROXY"]
    kwargs["proxies"] = {"http": proxy, "https": proxy}
response = requests.get(url, **kwargs)
```

### Python standard library

```python
import os, urllib.request
handlers = []
if os.environ["PENTEST_NETWORK_MODE"] == "proxy":
    proxy = os.environ["PENTEST_PROXY"]
    handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
else:
    handlers.append(urllib.request.ProxyHandler({}))
response = urllib.request.build_opener(*handlers).open(url, timeout=30)
```

### httpx / aiohttp

```python
# proxy is None in direct mode
proxy = os.environ.get("PENTEST_PROXY") if os.environ["PENTEST_NETWORK_MODE"] == "proxy" else None
with httpx.Client(proxy=proxy, timeout=30) as client:
    response = client.get(url)

# proxy_env.sh sets or unsets standard proxy variables
async with aiohttp.ClientSession(trust_env=True) as session:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
        ...
```

### Playwright / Chromium / Selenium

```python
launch = ({"proxy": {"server": os.environ["PENTEST_PROXY"]}}
          if os.environ["PENTEST_NETWORK_MODE"] == "proxy" else {})
browser = await playwright.chromium.launch(**launch)
# Add --proxy-server only in proxy mode for raw Chromium/CDP or Selenium.
```

### Other tools

- `wget`: standard proxy variables are set only in proxy mode.
- Node built-in `fetch`: configure an explicit dispatcher only in proxy mode.
- Custom sockets, raw TLS, DNS clients, and offline reversing tools may run directly when the ledger
  mode is `direct`.

## Evidence

CONNECT preflight carries `X-Redteam-Agent` and `X-Redteam-Engagement`. The ledger preserves both
successful proxy use and explicit fail-open decisions, but application-level settings cannot prove
that every library honored the selected mode.
