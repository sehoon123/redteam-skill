# Proxy Auto Policy

Default `PENTEST_PROXY_POLICY=auto` keeps offline analysis available without bypassing a reachable
intercepting proxy.

- reachable CONNECT: `proxy.checked`, network mode `proxy`
- connection unavailable: `proxy.unavailable`, warning, network mode `direct`
- reachable CONNECT rejection: `proxy.rejected`, blocked
- `required`: unavailable and rejected both block
- `off`: explicit direct/offline mode

The default endpoint is `${PENTEST_PROXY:-http://127.0.0.1:8080}`. This policy is fail-open by default
only for confirmed connection-level unavailability, never for a reachable rejection.

## Startup

`peer-start` combines join, proxy preflight, grounded claim, and brief:

```bash
. .pi/pentest/engagement_env.sh
python3 .pi/pentest/swarm.py peer-start --label "$LABEL" \
  --proxy-policy "${PENTEST_PROXY_POLICY:-auto}" --proxy "$PENTEST_PROXY"
```

`next` still refuses a proxy-required agent without trusted `proxy.checked` or allowed
`proxy.unavailable`. Generic `emit` cannot forge proxy events. `proxy_env.sh --agent "$AGENT"` remains
available for bounded offline/local tools, setting or unsetting `HTTP_PROXY`/`HTTPS_PROXY` and
`PENTEST_NETWORK_MODE` from the ledger.

Strict mode:

```bash
PENTEST_PROXY_POLICY=required pi
```

## Target HTTP traffic

Peers do not configure curl, Python requests, aiohttp, Playwright, Selenium, browser/CDP, or custom
sockets for target traffic. They call only:

```bash
python3 .pi/pentest/swarm.py exec-http \
  --agent "$AGENT" --work "$WORK" \
  --method GET --url "$URL" --check "$CHECK" --timeout 30
```

The host runner:

1. reads the latest trusted proxy event instead of ambient child shell variables;
2. uses explicit stdlib `ProxyHandler({"http": proxy, "https": proxy})` in proxy mode;
3. uses explicit `ProxyHandler({})` in direct mode;
4. validates URL host/port against the selected scope;
5. rejects URL credentials/fragments plus caller-owned provenance, `Host`, proxy, connection, and framing headers;
6. rechecks the live/non-stalled claim immediately before send;
7. disables redirect following, retries, and fallback;
8. injects agent/claim/experiment/engagement IDs;
9. enforces a total operation deadline and bounded body/header capture.

A redirect response is evidence but its destination is not requested. Expired/superseded claims cannot
send through the runner. Direct ad-hoc target traffic is outside the v3.7 contract because it cannot be
fenced or atomically checkpointed.

## Evidence and limitation

CONNECT preflight carries agent and engagement IDs. Application requests additionally carry claim and
experiment IDs. Request/response artifacts and a partial/error attempt are committed by the host runner.

Application-level policy cannot prove infrastructure egress isolation, and SQLite cannot make remote
HTTP exactly once across host process death. Operators still need network allowlists and kill switches.
