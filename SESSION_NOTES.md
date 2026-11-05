# Charging Automation — Session Notes

Last updated: 2026-11-05
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
Result: 22 tests, OK (3 skipped — live tests gated by HA_LIVE / SOLAREDGE_LIVE).

Live (not yet run this session):
    cd charging_automation && HA_LIVE=1 SOLAREDGE_LIVE=1 python -m unittest discover -s tests -v
Requires /app/config.json with v2 keys and /sessions/solaredge-token.json.
Bootstrap once if needed:
    cd charging_automation && python SolarEdgeAPI.py bootstrap /app/config.json <authorization_code>

## Commits this session

- c03af4c — docs: add night-time fields to credentials example config
- 4f7a196 — chore: update example config and pin dependencies
- 6b538ba — chore: remove stale credentials example; update config example and pin deps

## Known gaps / follow-ups

1. docker-compose.yml in the repo does NOT mount /sessions or /app/config.json.
   Production compose is different and already has a proper sessions mount —
   do not edit the repo copy without confirming with the user.
2. Live tests against Emporia + SolarEdge have not been run yet this session.
   Next step: run them and interpret output (pv/load/grid sanity check).
3. Dead code / minor cleanups (not blocking, not yet applied):
   - ChargingAutomation.py: unused ENABLE_NIGHT_MANAGEMENT constant;
     commented-out hysteresis block referencing undefined last_action_times
     and MIN_ACTION_INTERVAL; unused `soc = 0`; duplicated comment line in
     allocate_solar_watts.
   - RivianAPI.set_schedule_custom: `"amperage": self.AMPS_MAX` is set then
     overwritten in both branches (dead initial value).
   - RivianAPI.init_vehicle_info: will KeyError if
     data['data']['vehicleState'] is None (chargerStatus check happens after
     indexing vehicleState).
   - TeslaAPI._run_async: `except (OAuthExpired, Exception)` is redundant.
   - HubitatAPI.update_info_device_message: message is embedded in the URL
     unencoded; should use requests params or urllib.parse.quote.
4. No tests yet for ChargingAutomation.py (allocate_solar_watts,
   watts_to_amps, is_night_time, get_automation_mode) or for
   HubitatAPI/RivianAPI/TeslaAPI.

## Conventions

- Python 3, unittest (not pytest), tests under charging_automation/tests/.
- Tests insert the parent dir on sys.path so they run from repo root.
- Live tests are gated by env vars (HA_LIVE=1, SOLAREDGE_LIVE=1) and read
  /app/config.json.
- Logging via module-level `logger = logging.getLogger(__name__)`.
- Config is a plain class reading /app/config.json with required keys
  accessed via data['key'] (KeyError on missing) and optional via .get().
