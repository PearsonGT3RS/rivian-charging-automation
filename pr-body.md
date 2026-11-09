## Summary
- Migrate SolarEdge integration from v1 (static API key) to v2 (OAuth 2.0 with rotating refresh tokens).
- Source EV load from Emporia Vue sensors via Home Assistant; compute `grid = load - pv`.
- Add unit tests for HubitatAPI, RivianAPI, TeslaAPI, ChargingAutomation, HomeAssistantAPI, SolarEdgeAPI.
- Rewrite Dockerfile for `python:3.12-slim-bookworm`, non-root `app` user, and `/sessions` mount point.
- Harden Tesla OAuth handling: catch `OAuthExpired` (which does not inherit from `Exception`), refresh tokens, and retry once.
- Fix SolarEdge v2 OAuth token requests to use `application/x-www-form-urlencoded` (JSON caused HTTP 500).
- Guard against transient SolarEdge zero-PV readings during daylight hours.

## Changes
- `SolarEdgeAPI.py`: v2 OAuth flow, token rotation, 401 retry, ISO-8601 UTC timestamps for `/power`, `LoadUnavailableError` when load is unavailable. Token requests now use `application/x-www-form-urlencoded` (RFC 6749) instead of JSON — SolarEdge returns HTTP 500 on JSON bodies. Refresh failures now log the response body and, on `invalid_grant` / 500, tell the operator to re-run the OAuth bootstrap. `/power` lookback widened from 30 → 60 minutes so a single delayed/null bucket doesn't force a stale/zero sample.
- `HomeAssistantAPI.py`: new — Emporia load source.
- `ChargingAutomation.py`: wire `load_provider`, catch `LoadUnavailableError`, use `power_flow.grid`. Added diagnostic logging of `pv` / `load` / `grid`. Added a guard: if `pv <= 0` between 08:00 and 17:00 local, skip the cycle (return `DAYTIME_ACTIVE`) rather than acting on a transient SolarEdge reporting gap.
- `TeslaAPI.py`: catch `OAuthExpired` explicitly (it does not inherit from `Exception` in `tesla-fleet-api`; matched by class name + message so duplicate module instances can't defeat the check). On expiry, refresh tokens and retry once via `_retry_after_refresh()`. Added `force_refresh_tokens()` for manual refresh. Refresh failures now log the HTTP status and body.
- `Config.py`: expose v2 + HA + night-time fields.
- `main.py`: dispatch on `CycleResult` enum (NIGHT_ACTIVE → 5 min, DAYTIME_ACTIVE → 15 min, IDLE → 30 min).
- `config-example.json`, `requirements.txt`: updated for v2.
- `credentials-example.json`: removed (v1 leftover).
- `Dockerfile`: slim-bookworm base, non-root user, `/sessions` dir, playwright chromium.
- Tests: `test_solaredge.py`, `test_homeassistant.py`, `test_charging_automation.py`, `test_hubitat.py`, `test_rivian.py`, `test_tesla.py`. Tesla tests include regression coverage for the `OAuthExpired` refresh-and-retry path and `force_refresh_tokens()`.

## Test plan
- [x] `cd charging_automation && python -m unittest discover -s tests -v` → 108 tests OK (3 skipped)
- [x] `SOLAREDGE_LIVE=1 python -m unittest tests.test_solaredge.TestSolarEdgeLive -v` → 2 tests OK (pv=2633W load=8644W grid=6011W)
- [x] `HA_LIVE=1 python -m unittest tests.test_homeassistant.TestHomeAssistantLive -v` → 1 test OK (Emporia total load = 1399W)
- [x] Tesla `OAuthExpired` refresh-and-retry path verified in production (commit e3a55a9)
- [x] SolarEdge sign / transient-zero-PV issue diagnosed and fixed (commits fd50e45, f88a93d)
- [x] SolarEdge OAuth token requests switched to form-encoded (commit be54b31); refresh failures now surface actionable errors (commit ebef7ea)
- [ ] `TESLA_LIVE=1 python -m unittest tests.test_tesla.TestTeslaLive -v` (read-only; pending)
- [ ] `RIVIAN_LIVE=1 python -m unittest tests.test_rivian.TestRivianLive -v` (read-only; pending)
- [ ] `HA_LIVE=1 SOLAREDGE_LIVE=1 TESLA_LIVE=1 RIVIAN_LIVE=1 python -m unittest discover -s tests -v` (full live suite — pending)
- [ ] SolarEdge OAuth re-bootstrap (refresh token expired during the JSON-body bug window; requires one-time out-of-band re-auth)

## Known issues
- The SolarEdge refresh token is currently expired (invalidated after 14 days of non-use while the JSON-body bug prevented refreshes). A one-time OAuth re-bootstrap is required:
  ```
  cd charging_automation && python SolarEdgeAPI.py bootstrap /app/config.json <authorization_code>
  ```
  The `redirect_uri` used in the authorize URL must match the value registered on the SolarEdge app exactly (URL-encoded). `error=400.9` from SolarEdge means "invalid redirect_uri".
