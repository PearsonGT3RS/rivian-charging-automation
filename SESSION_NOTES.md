# Charging Automation — Session Notes

Last updated: 2026-11-06
Repo: charging_automation (SolarEdge v2 migration)

## How to resume

Paste this file's contents into a new chat, then add these files to the chat:

- charging_automation/ChargingAutomation.py
- charging_automation/Config.py
- charging_automation/HomeAssistantAPI.py
- charging_automation/HubitatAPI.py
- charging_automation/RivianAPI.py
- charging_automation/SolarEdgeAPI.py
- charging_automation/TeslaAPI.py
- charging_automation/main.py
- charging_automation/config-example.json
- charging_automation/requirements.txt
- charging_automation/tests/test_charging_automation.py
- charging_automation/tests/test_homeassistant.py
- charging_automation/tests/test_solaredge.py
- docker-compose.yml

## Goal

Migrate the charging automation from the SolarEdge v1 API (static API key)
to the SolarEdge v2 API (OAuth 2.0 Bearer tokens with rotating refresh
tokens), and source EV load from Emporia Vue sensors exposed via Home
Assistant so we can compute grid flow (grid = load - pv).

## Status: migration complete, local tests green

### Confirmed complete

- SolarEdgeAPI.py — v2 OAuth flow, token rotation, 401 retry, PowerFlow
  interface preserved. LoadUnavailableError raised when load_provider
  returns None.
- HomeAssistantAPI.py — Emporia load source; returns None on
  unavailable/unknown/non-numeric/HTTP error so LoadUnavailableError fires.
- ChargingAutomation.py — wires load_provider from HA + emporia_load_entities,
  catches LoadUnavailableError, uses power_flow.grid.
- Config.py — exposes solaredge_client_id/secret/site_id/redirect_uri,
  homeassistant_url/token, emporia_load_entities, night_time_start/end.
- main.py — respects True/False return contract (5-min vs 30-min sleep).
- config-example.json — updated to v2 schema (client-id/secret/redirect-uri,
  homeassistant-*, emporia-load-entities, night-time-start/end).
- requirements.txt — pinned requests==2.31.0, PyJWT>=2.8.0,
  tesla-fleet-api>=1.4.0, cryptography>=42.0.0, aiohttp>=3.9.0.
- credentials-example.json — deleted (v1 leftover, nothing reads it).

### Test status

Local (no network):
    cd charging_automation && python -m unittest discover -s tests -v
Result: 46 tests, OK (3 skipped — live tests gated by HA_LIVE / SOLAREDGE_LIVE).

Live (not yet run — still pending):
    cd charging_automation && HA_LIVE=1 SOLAREDGE_LIVE=1 python -m unittest discover -s tests -v
Requires /app/config.json with v2 keys and /sessions/solaredge-token.json.
Bootstrap once if needed:
    cd charging_automation && python SolarEdgeAPI.py bootstrap /app/config.json <authorization_code>

SolarEdge-only live run (next step):
    cd charging_automation && SOLAREDGE_LIVE=1 python -m unittest tests.test_solaredge.TestSolarEdgeLive -v
Expected output:
    [LIVE] SolarEdge current PV power: <N> W
    [LIVE] pv=<N>W load=<N>W grid=<N>W
Interpret pv/load/grid after the run (grid sign: positive = import, negative = export).

## Commits this session

- c03af4c — docs: add night-time fields to credentials example config
- 4f7a196 — chore: update example config and pin dependencies
- 6b538ba — chore: remove stale credentials example; update config example and pin deps
- 7c575b6 — chore: clean up stale comments and dead code in charging automation
  (removed unused ENABLE_NIGHT_MANAGEMENT, commented-out hysteresis block,
  unused `soc = 0`, duplicated comment in allocate_solar_watts; RivianAPI
  vehicleState None guard; TeslaAPI redundant except cleanup; HubitatAPI
  URL-encoding comment)
- d8fdc16 — fix: patch ChargingAutomation.datetime with fake class in
  night-time tests (replaces the immutable datetime.datetime.now patch)
- 05e7d65 — fix: handle same-day night window in is_night_time
  (start <= end now uses `start <= hour < end`; wrapping window still uses
  `hour >= start or hour < end`)

## Known gaps / follow-ups

1. docker-compose.yml in the repo does NOT mount /sessions or /app/config.json.
   Production compose is different and already has a proper sessions mount —
   do not edit the repo copy without confirming with the user.
2. Live tests against Emporia + SolarEdge have not been run yet.
   Next step: run the SolarEdge-only live test and interpret pv/load/grid.
   (2026-11-06 session: command + prerequisites were provided to the user;
   the run itself was not executed in-session.)
3. Dead code / minor cleanups — DONE in 7c575b6. Notes:
   - RivianAPI.set_schedule_custom: the dead initial `"amperage": self.AMPS_MAX`
     was already gone in the current file; both branches set it explicitly.
   - HubitatAPI.update_info_device_message: message was already passed via
     `params={'value': message}` (requests URL-encodes it); only a clarifying
     comment was added.
4. Tests for ChargingAutomation.py added (tests/test_charging_automation.py)
   covering allocate_solar_watts, watts_to_amps, is_night_time,
   get_automation_mode. All passing after d8fdc16 + 05e7d65.
   Still no tests for HubitatAPI / RivianAPI / TeslaAPI.
   (2026-11-06 session: HubitatAPI.py, RivianAPI.py, TeslaAPI.py, and all
   three test files were added to the chat — ready to start writing those
   tests next session.)
5. is_night_time bug fixed in 05e7d65: same-day windows (start <= end) were
   previously treated as wrapping windows, causing hour=0 to be flagged as
   night for a 01:00–05:00 window.

## Next session

1. Run the SolarEdge-only live test and interpret pv/load/grid:
       cd charging_automation && SOLAREDGE_LIVE=1 python -m unittest tests.test_solaredge.TestSolarEdgeLive -v
2. If green, run the full live suite (HA + SolarEdge):
       cd charging_automation && HA_LIVE=1 SOLAREDGE_LIVE=1 python -m unittest discover -s tests -v
3. Add unit tests for HubitatAPI / RivianAPI / TeslaAPI (files already in chat).
   Follow existing conventions: unittest, sys.path insert, mock requests /
   _run_async, no network.

## Conventions

- Python 3, unittest (not pytest), tests under charging_automation/tests/.
- Tests insert the parent dir on sys.path so they run from repo root.
- Live tests are gated by env vars (HA_LIVE=1, SOLAREDGE_LIVE=1) and read
  /app/config.json.
- Logging via module-level `logger = logging.getLogger(__name__)`.
- Config is a plain class reading /app/config.json with required keys
  accessed via data['key'] (KeyError on missing) and optional via .get().
