## Summary
- Migrate SolarEdge integration from v1 (static API key) to v2 (OAuth 2.0 with rotating refresh tokens).
- Source EV load from Emporia Vue sensors via Home Assistant; compute `grid = load - pv`.
- Add unit tests for HubitatAPI, RivianAPI, TeslaAPI, ChargingAutomation, HomeAssistantAPI, SolarEdgeAPI.
- Rewrite Dockerfile for `python:3.12-slim-bookworm`, non-root `app` user, and `/sessions` mount point.

## Changes
- `SolarEdgeAPI.py`: v2 OAuth flow, token rotation, 401 retry, ISO-8601 UTC timestamps for `/power`, `LoadUnavailableError` when load is unavailable.
- `HomeAssistantAPI.py`: new — Emporia load source.
- `ChargingAutomation.py`: wire `load_provider`, catch `LoadUnavailableError`, use `power_flow.grid`.
- `Config.py`: expose v2 + HA + night-time fields.
- `main.py`: respect True/False return contract (5-min vs 30-min sleep).
- `config-example.json`, `requirements.txt`: updated for v2.
- `credentials-example.json`: removed (v1 leftover).
- `Dockerfile`: slim-bookworm base, non-root user, `/sessions` dir, playwright chromium.
- Tests: `test_solaredge.py`, `test_homeassistant.py`, `test_charging_automation.py`, `test_hubitat.py`, `test_rivian.py`, `test_tesla.py`.

## Test plan
- [x] `cd charging_automation && python -m unittest discover -s tests -v` → 103 tests OK (1 skipped)
- [x] `SOLAREDGE_LIVE=1 python -m unittest tests.test_solaredge.TestSolarEdgeLive -v` → 2 tests OK (pv=2633W load=8644W grid=6011W)
- [ ] `HA_LIVE=1 SOLAREDGE_LIVE=1 python -m unittest discover -s tests -v` (full live suite — pending)
