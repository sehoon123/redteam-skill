# Mandatory Target Proxy

All target-facing traffic must traverse `${PENTEST_PROXY:-http://127.0.0.1:8080}`. There is no
direct fallback. If proxy preflight fails, do not contact the target; checkpoint and leave.

## Required startup

```bash
. .pi/pentest/proxy_env.sh
python3 .pi/pentest/swarm.py proxy-check --agent "$AGENT" \
  --proxy "$PENTEST_PROXY" --timeout 5
```

`join` is proxy-required by default (`--proxy-required` is kept explicit in peer profiles). `next`
refuses a lease until the dedicated `proxy-check` command has
successfully opened a scoped CONNECT tunnel and recorded `proxy.checked`. Generic `emit` cannot
forge this event.

Source `proxy_env.sh` in every new shell tool call; shell environments do not persist between calls.

## Tool settings

### curl

```bash
. .pi/pentest/proxy_env.sh
curl --proxy "$PENTEST_PROXY" \
  --proxy-header "X-Redteam-Agent: $AGENT" \
  --connect-timeout 10 --max-time 30 https://ginandjuice.shop/
```

### Python requests

```python
import os, requests
proxy = os.environ.get("PENTEST_PROXY", "http://127.0.0.1:8080")
with requests.Session() as session:
    session.proxies.update({"http": proxy, "https": proxy})
    response = session.get("https://ginandjuice.shop/", timeout=30)
```

### Python standard library

```python
import os, urllib.request
proxy = os.environ.get("PENTEST_PROXY", "http://127.0.0.1:8080")
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": proxy, "https": proxy})
)
response = opener.open("https://ginandjuice.shop/", timeout=30)
```

### httpx / aiohttp

```python
# httpx
with httpx.Client(proxy=proxy, timeout=30) as client:
    response = client.get(url)

# aiohttp: environment variables are ignored unless trust_env=True
async with aiohttp.ClientSession(trust_env=True) as session:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
        ...
```

### Playwright / Chromium / Selenium

```python
browser = await playwright.chromium.launch(proxy={"server": proxy})
# Raw Chromium/CDP: chromium --proxy-server="$PENTEST_PROXY" ...
# Selenium ChromeOptions: options.add_argument(f"--proxy-server={proxy}")
```

### Other tools

- `wget`: set both lowercase proxy environment variables or pass its explicit proxy options.
- Node built-in `fetch`: environment variables may be ignored; configure an explicit proxy dispatcher.
- Custom sockets, raw TLS, DNS clients, and libraries without proxy support are forbidden for target
  traffic unless operator infrastructure independently blocks direct egress.

## Evidence

The local proxy log and ledger `proxy.checked` event establish preflight use. They do not prove every
later library honored proxy settings. Production enforcement therefore also requires infrastructure
egres rules that permit target traffic only from the proxy process.
