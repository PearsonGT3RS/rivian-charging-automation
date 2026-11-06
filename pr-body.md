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
- `main.py`: dispatch on `CycleResult` enum (NIGHT_ACTIVE → 5 min, DAYTIME_ACTIVE → 15 min, IDLE → 30 min).
- `config-example.json`, `requirements.txt`: updated for v2.
- `credentials-example.json`: removed (v1 leftover).
- `Dockerfile`: slim-bookworm base, non-root user, `/sessions` dir, playwright chromium.
- Tests: `test_solaredge.py`, `test_homeassistant.py`, `test_charging_automation.py`, `test_hubitat.py`, `test_rivian.py`, `test_tesla.py`.

## Test plan
- [x] `cd charging_automation && python -m unittest discover -s tests -v` → 108 tests OK (3 skipped)
- [x] `SOLAREDGE_LIVE=1 python -m unittest tests.test_solaredge.TestSolarEdgeLive -v` → 2 tests OK (pv=2633W load=8644W grid=6011W)
- [x] `HA_LIVE=1 python -m unittest tests.test_homeassistant.TestHomeAssistantLive -v` → 1 test OK (Emporia total load = 1399W)
- [ ] `TESLA_LIVE=1 python -m unittest tests.test_tesla.TestTeslaLive -v` (read-only; pending)
- [ ] `RIVIAN_LIVE=1 python -m unittest tests.test_rivian.TestRivianLive -v` (read-only; pending)
- [ ] `HA_LIVE=1 SOLAREDGE_LIVE=1 TESLA_LIVE=1 RIVIAN_LIVE=1 python -m unittest discover -s tests -v` (full live suite — pending)
