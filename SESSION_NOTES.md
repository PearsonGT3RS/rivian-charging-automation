# Charging Automation — Session Notes

Last updated: 2026-11-10 (session 5)
Repo: charging_automation (SolarEdge v2 migration)
Branch: solaredge-v2 (PR pending to main/master)

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
- charging_automation/tests/test_rivian.py
- charging_automation/tests/test_tesla.py
- charging_automation/tests/test_hubitat.py
- charging_automation/tests/__init__.py
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
  returns None. `get_current_power_flow()` accepts an optional
  `pv_override` so callers can supply PV from an alternate source (HA)
  while still reusing the PowerFlow construction and logging.
  `KEEPALIVE_INTERVAL_SECONDS` is now 6 days (was 7).
- HomeAssistantAPI.py — Emporia load source; returns None on
  unavailable/unknown/non-numeric/HTTP error so LoadUnavailableError fires.
- ChargingAutomation.py — wires load_provider from HA + emporia_load_entities,
  catches LoadUnavailableError, uses power_flow.grid. Prefers HA PV
  (lead + follower inverter sensors) with SolarEdge API fallback. Runs
  the SolarEdge keepalive refresh unconditionally at the top of every
  cycle (day and night). `validate_emporia_config()` warns at startup if
  a per-EV entity is configured but not aggregated into
  emporia-load-entities.
- Config.py — exposes solaredge_client_id/secret/site_id/redirect_uri,
  homeassistant_url/token, emporia_load_entities, emporia_rivian_entity,
  emporia_tesla_entity, emporia_pv_lead_entity, emporia_pv_follower_entity,
  night_time_start/end.
- main.py — dispatches on CycleResult enum (NIGHT_ACTIVE → 5 min,
  DAYTIME_ACTIVE → 15 min, IDLE → 30 min).
- config-example.json — updated to v2 schema (client-id/secret/redirect-uri,
  homeassistant-*, emporia-load-entities, night-time-start/end).
- requirements.txt — pinned requests==2.31.0, PyJWT>=2.8.0,
  tesla-fleet-api>=1.4.0, cryptography>=42.0.0, aiohttp>=3.9.0.
- credentials-example.json — deleted (v1 leftover, nothing reads it).

### Test status

Local (no network):
    cd charging_automation && python -m unittest discover -s tests -v
Result: 146 tests, OK (8 skipped — live tests gated by
HA_LIVE / SOLAREDGE_LIVE / TESLA_LIVE / RIVIAN_LIVE; Tesla unit tests
run when tesla-fleet-api / cryptography are installed).

Live tests are gated by env vars and are read-only:
    HA_LIVE=1 SOLAREDGE_LIVE=1 TESLA_LIVE=1 RIVIAN_LIVE=1 \
      python -m unittest discover -s tests -v
Requires /app/config.json with v2 keys, /sessions/solaredge-token.json,
/sessions/tesla-session.json, /sessions/rivian-session.json.
Bootstrap SolarEdge once if needed:
    cd charging_automation && python SolarEdgeAPI.py bootstrap /app/config.json <authorization_code>

SolarEdge-only live run — PASSED 2026-11-06:
    cd charging_automation && SOLAREDGE_LIVE=1 python -m unittest tests.test_solaredge.TestSolarEdgeLive -v
Observed output:
    [LIVE] SolarEdge current PV power: 2633 W
    [LIVE] pv=2633W load=8644W grid=6011W
Interpretation: grid is positive (importing ~6kW) — expected mid-day with
EV charging + house load exceeding PV. Sign convention confirmed:
positive = import, negative = export.

Home Assistant live run — PASSED 2026-11-06:
    cd charging_automation && HA_LIVE=1 python -m unittest tests.test_homeassistant.TestHomeAssistantLive -v
Observed output:
    [LIVE] sensor.right_panel_connection_power_minute_average = 913 W
    [LIVE] sensor.left_side_connection_power_minute_average = 486 W
    [LIVE] Emporia total load = 1399 W

Tesla / Rivian live tests — added but not yet run. Both are read-only
(no wake, no charge start/stop, no amp changes). Run with:
    TESLA_LIVE=1 python -m unittest tests.test_tesla.TestTeslaLive -v
    RIVIAN_LIVE=1 python -m unittest tests.test_rivian.TestRivianLive -v

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
- 5e1be36 — fix: format SolarEdge v2 power timestamps as ISO-8601 UTC instants
  (v2 /power rejects bare local timestamps with 400 "Failed to convert ...
  to required type [Instant]"; now appends 'Z' to from/to)
- d05ed4a — test: add unit tests for HubitatAPI, RivianAPI, and TeslaAPI
  (tests/test_hubitat.py, tests/test_rivian.py, tests/test_tesla.py)
- a129941 — fix: remove extra mock response in login session test
  (first init_user_info() short-circuits without a POST when no session
  file exists, so the side_effect list had one response too many)
- c88c33f — docs: update session notes with test results and next steps
- 76e09e4 — docs: add PR body for SolarEdge v2 migration (pr-body.md)
- 79afadf — chore: add se.sh to gitignore
- (uncommitted) build: rewrite Dockerfile for slim-bookworm, non-root user,
  and /sessions mount (python:3.12-slim-bookworm, app user, playwright
  chromium, /app + /sessions dirs)

### Session 2 commits (SolarEdge v2 follow-ups)

- 3db84ef — refactor: extract sleep intervals into constants and remove
  redundant retry call (main.py)
- b813a95 — refactor: cache Rivian schedules per cycle and add daytime
  interval constant (RivianAPI.set_schedule_custom accepts optional
  current_schedules; get_current_schedule_amp guards against None/empty;
  ChargingAutomation fetches schedules once and reuses)
- 90fdbaf — feat: return CycleResult enum from automation cycle to select
  sleep interval (IDLE / NIGHT_ACTIVE / DAYTIME_ACTIVE; main.py dispatches
  on the enum)
- ee3819a — test: add CycleResult and early-exit tests for
  run_charging_automation
- 26d30fc — test: replace incorrect enum truthiness assertions with
  distinctness checks (Enum members are always truthy regardless of .value)
- ba02047 — test: add gated live tests for Tesla and Rivian (read-only;
  TESLA_LIVE=1 / RIVIAN_LIVE=1)
- f471080 — fix: handle null values in SolarEdge current power samples
  (walk backwards to newest non-null sample; return None if all null)

### Session 5 commits (test fixes + live HA PV test + config docs)

- 327dd73 — test: add live HA PV sensor test
  (`TestHomeAssistantLive.test_live_pv_sensors` reads
  `emporia-pv-lead-entity` / `emporia-pv-follower-entity`, asserts both
  are numeric and non-negative, prints the PV total; skips if either key
  is unset)
- 0c8ac4a — test: fix fake config and vehicle setup in charging automation
  tests (`_FakeConfigWithEmporia` now distinguishes `load_entities=None`
  from `load_entities=[]`; `TestPvSourceSelection` and
  `TestKeepaliveAlwaysRuns` now mark at least one vehicle as connected so
  the cycle reaches the daytime power-flow / keepalive steps instead of
  returning at the "No chargers plugged in" early exit)
- d313e7c — docs: add emporia PV lead and follower entity examples to config
  (`config-example.json` now includes `emporia-pv-lead-entity` and
  `emporia-pv-follower-entity`)

### Session 4 commits (HA PV preference + keepalive)

- cd71187 — feat: add Emporia config validation warning and daytime cycle
  tests (`validate_emporia_config()` warns when a per-EV entity is set but
  not in `emporia-load-entities`; `TestValidateEmporiaConfig`,
  `TestRunChargingAutomationDaytimeCycle`, and a `LoadUnavailableError`
  early-exit test added)
- c00e0bf — feat: prefer HA PV sensors with SolarEdge fallback and 6-day
  keepalive (new `emporia-pv-lead-entity` / `emporia-pv-follower-entity`
  config keys; `pv_provider` sums the two inverter sensors; SolarEdge
  `get_current_power_flow()` gains `pv_override`; keepalive moved out of
  the night branch and runs on every cycle; `KEEPALIVE_INTERVAL_SECONDS`
  reduced from 7 to 6 days; `TestPvSourceSelection` and
  `TestKeepaliveAlwaysRuns` added)

### Session 3 commits (EV draw / solar allocation fix)

- f9eebbf — fix: use commanded amps for EV draw in solar allocation
  (initial attempt: replaced is_charging() gate with commanded amps only)
- 363e447 — fix: gate EV current draw on is_charging to avoid phantom watt
  allocation (refinement: use commanded amps as the *value*, but only when
  is_charging() is True — avoids both under-counting during a wake/ramp and
  over-counting from stale commanded amps after a session ends)
- 602c46d — docs: update session notes with solar allocation logic and
  session 3 commits
- 4b480b4 — fix: estimate EV draw from recent commanded amps to handle
  wake/ramp window (adds module-level `_last_commanded` cache with a
  20-minute TTL; `apply_charging()` records the commanded amps, and the
  draw calculation trusts that cache when `is_charging()` is False but a
  recent command exists. Falls back to `live_amps * VOLTS` only when
  `is_charging()` is True and no recent command is cached.)
- 75d41ff — fix: cap EV draw estimate at measured load to prevent stale API
  over-count (superseded by 2351063)
- 2351063 — feat: read per-EV draw from dedicated Emporia sensors
  (replaces the `_last_commanded` cache and the load-cap heuristic with
  ground-truth readings from `sensor.rivian_charger_power_minute_average`
  and `sensor.external_lighting_power_minute_average`; adds
  `emporia-rivian-entity` / `emporia-tesla-entity` config keys)
- (pending) test: add EV-draw and per-EV Emporia sensor tests
  (test_charging_automation.py: TestRunChargingAutomationEvDraw,
  TestConfigEmporiaEntities; test_homeassistant.py: per-EV sensor unit
  tests + live per-EV sensor test)

## PV source selection (as of session 4, commit c00e0bf)

`run_charging_automation()` prefers Home Assistant PV sensors and falls
back to the SolarEdge API:

1. If `emporia-pv-lead-entity` and `emporia-pv-follower-entity` are both
   configured, `pv_provider()` reads both sensors and returns their sum.
   If either sensor is unavailable (`None`), `pv_provider()` returns
   `None`.
2. If `pv_provider()` returns a value, it is passed to
   `SolarEdgeAPI.get_current_power_flow(pv_override=...)` and the
   SolarEdge `/power` endpoint is not called.
3. Otherwise `pv_override=None` and `get_current_power_flow()` falls back
   to `get_current_power()` (SolarEdge v2 `/power`).
4. The chosen source is logged each cycle as `PV source: homeassistant`
   or `PV source: solaredge`.

The two inverter sensors are Modbus-backed Emporia sensors exposed via
Home Assistant. They must be summed because the site has two inverters
(lead + follower).

## SolarEdge token keepalive (as of session 4, commit c00e0bf)

SolarEdge v2 invalidates refresh tokens after 14 days of non-use. To
avoid the full OAuth dance, `run_charging_automation()` calls
`SolarEdgeAPI._maybe_keepalive_refresh()` at the top of **every** cycle
(day and night), regardless of whether HA PV is the primary source. The
refresh fires at most once per `KEEPALIVE_INTERVAL_SECONDS` (6 days),
leaving an 8-day margin before the 14-day expiry.

## Solar allocation logic (as of session 3, commit 2351063)

`run_charging_automation()` computes the total solar budget for the EVs as:

    solar_for_ev = current_power + available_power

where:
- `available_power = -power_flow.grid` (positive = surplus / export).
- `current_power = rivian_draw + tesla_draw`, read directly from the
  dedicated Emporia circuit sensors:
    - `emporia-rivian-entity` (e.g.
      `sensor.rivian_charger_power_minute_average`)
    - `emporia-tesla-entity` (e.g.
      `sensor.external_lighting_power_minute_average`)

  These sensors are **already aggregated into** `emporia-load-entities`
  (the top-level load sensors), so `load` includes the EV draw. Adding the
  per-EV draw back to the surplus therefore yields the total solar budget:

      solar_for_ev = ev_draw + (pv - load)
                   = ev_draw + (pv - house - ev_draw)
                   = pv - house

  which is exactly the solar available for the EVs regardless of how much
  they are currently drawing.

  If a per-EV sensor is not configured or returns `None` (unavailable), that
  vehicle's draw is treated as 0. This is conservative: we under-count the
  EV draw, so `solar_for_ev` is smaller than it could be, and we never
  command more than the actual surplus. Negative sensor values are clamped
  to 0. Disconnected vehicles are not read at all.

  This replaces the earlier `_last_commanded` cache (4b480b4) and the
  load-cap heuristic (75d41ff), both of which were workarounds for the
  unreliability of inferring EV draw from the vehicle API. The Emporia
  sensors are ground truth and eliminate the wake/ramp window and stale
  `charge_amps` failure modes entirely.

`allocate_solar_watts(solar_for_ev, rivian_soc, tesla_soc, rivian_connected,
tesla_connected)` then splits the budget:
- No vehicles connected → (0, 0).
- Single vehicle connected → gets everything, unless SOC ≥ 100 (→ 0,0) or
  SOC > 50 with solar_for_ev ≤ 0 (→ 0,0).
- Both connected, both SOC > 50, no surplus → (0, 0) (Rule 4).
- Both connected, total == 0 → (0, 0).
- Split ratio by SOC:
    - Both ≥ 100 → (0, 0).
    - One ≥ 100 → other gets 100%.
    - Both ≤ 50 → 50/50.
    - Otherwise → lower-SOC vehicle gets 75%, higher gets 25%.
- Minimum-watt reassignment: if a vehicle's share < its hardware minimum
  (Rivian 1920W, Tesla 1200W), its share is reassigned to the other vehicle
  (capped at that vehicle's AMPS_MAX × 240V). If both shares are below
  minimum → (0, 0).

The EV-draw changes (363e447, 4b480b4, 75d41ff, 2351063) only affect the
*input* to the split (`solar_for_ev`); the split ratios and minimum-watt
reassignment are unchanged and remain correct.

### Why the vehicle-API approaches were insufficient

Three successive attempts to infer EV draw from the vehicle API all failed
in production:

1. **363e447** — gate on `is_charging()`. Fixed the phantom-watt case but
   reintroduced the under-count during the wake/ramp window. Observed
   2026-11-09:

       Power flow: pv=6331W load=5767W grid=-564W
       Current EV Draw: 0W | Total Solar for EVs: 564W
       Tesla: target 564W → 2A (Currently Charging: False)

   The Tesla was drawing power (load=5767W includes it) but reported
   `charging_state != 'Charging'`.

2. **4b480b4** — trust the last commanded amps for 20 minutes. Fixed the
   wake/ramp under-count but could over-count if the car finished a session
   and the commanded amps persisted.

3. **75d41ff** — cap the estimate at the measured load. Fixed the
   over-count but still relied on the vehicle API for the value.

4. **2351063** — read the draw directly from the dedicated Emporia circuit
   sensors. This is ground truth: no inference, no staleness, no wake/ramp
   window. The `_last_commanded` cache and the load-cap heuristic are both
   removed.

## Known gaps / follow-ups

0. Local test environment: `python3 -m unittest tests.test_charging_automation`
   fails with `ModuleNotFoundError: No module named 'requests'` unless
   dependencies are installed first:
       pip3 install -r charging_automation/requirements.txt
   (The tests themselves only use stdlib `unittest`/`mock`, but importing
   `ChargingAutomation` pulls in `HomeAssistantAPI`, which imports
   `requests`.)

1. docker-compose.yml in the repo does NOT mount /sessions or /app/config.json.
   Production compose is different and already has a proper sessions mount —
   do not edit the repo copy without confirming with the user.
   NOTE: the new Dockerfile creates /app and /sessions and runs as the `app`
   user, so the production compose must mount both paths with matching
   ownership (or the container will fail to read config/token files).
2. SolarEdge-only live test PASSED 2026-11-06 (pv=2633W load=8644W grid=6011W).
   HA-only live test PASSED 2026-11-06 (Emporia total load = 1399W).
   Tesla / Rivian live tests added but not yet run:
       TESLA_LIVE=1 python -m unittest tests.test_tesla.TestTeslaLive -v
       RIVIAN_LIVE=1 python -m unittest tests.test_rivian.TestRivianLive -v
3. Dead code / minor cleanups — DONE in 7c575b6. Notes:
   - RivianAPI.set_schedule_custom: the dead initial `"amperage": self.AMPS_MAX`
     was already gone in the current file; both branches set it explicitly.
   - HubitatAPI.update_info_device_message: message was already passed via
     `params={'value': message}` (requests URL-encodes it); only a clarifying
     comment was added.
4. Tests for ChargingAutomation.py added (tests/test_charging_automation.py)
   covering allocate_solar_watts, watts_to_amps, is_night_time,
   get_automation_mode. All passing after d8fdc16 + 05e7d65.
   Tests for HubitatAPI / RivianAPI / TeslaAPI added in d05ed4a
   (tests/test_hubitat.py, tests/test_rivian.py, tests/test_tesla.py).
   Tesla tests are gated by _TESLA_DEPS_AVAILABLE and skip when
   tesla-fleet-api / cryptography are not installed.
5. is_night_time bug fixed in 05e7d65: same-day windows (start <= end) were
   previously treated as wrapping windows, causing hour=0 to be flagged as
   night for a 01:00–05:00 window.
6. SECURITY: se.sh (OAuth bootstrap curl with client_secret + authorization
   code) was added to .gitignore in 79afadf. The file was never committed
   (git log --all -- se.sh is empty), so no history rewrite is needed.
   The client_secret and authorization code in that file should still be
   treated as compromised — rotate the SolarEdge client secret in the admin
   console and re-run the OAuth bootstrap.
7. Git push to origin failed with "Password authentication is not supported
   for Git operations." Use a PAT (HTTPS) or SSH key, or `gh auth login`.
   See PR body in pr-body.md.

## Next session

1. Commit the Dockerfile rewrite (currently uncommitted on solaredge-v2):
       git add Dockerfile
       git commit -m "build: rewrite Dockerfile for slim-bookworm, non-root user, and /sessions mount"
2. Push solaredge-v2 and open the PR (see pr-body.md):
       git push -u origin solaredge-v2
       gh pr create --base main --head solaredge-v2 \
         --title "Migrate to SolarEdge v2 OAuth + Emporia load via Home Assistant" \
         --body-file pr-body.md
   (If push fails with "Password authentication is not supported", configure
   a PAT or SSH key first — see Known gaps #7.)
3. Rotate the SolarEdge client secret (see Known gaps #6) and re-run the
   OAuth bootstrap to regenerate /sessions/solaredge-token.json.
4. Run the full live suite (HA + SolarEdge):
       cd charging_automation && HA_LIVE=1 SOLAREDGE_LIVE=1 python -m unittest discover -s tests -v
5. Confirm the Tesla unit tests actually run in an environment where
   tesla-fleet-api / cryptography are installed (currently skipped locally).
6. Run the Tesla and Rivian live tests (read-only) to confirm connectivity:
       TESLA_LIVE=1 python -m unittest tests.test_tesla.TestTeslaLive -v
       RIVIAN_LIVE=1 python -m unittest tests.test_rivian.TestRivianLive -v
7. Run the live HA PV test against the real Home Assistant instance:
       cd charging_automation && HA_LIVE=1 python -m unittest tests.test_homeassistant.TestHomeAssistantLive -v
   Confirm `test_live_pv_sensors` prints both inverter readings and a
   non-zero PV total during daylight.
8. Verify the HA PV preference in production: confirm the log line
   `PV source: homeassistant` appears during daylight cycles and that
   `pv` matches the sum of the two inverter sensors.
   - `test_charging_automation.py::TestPvSourceSelection` — verifies HA
     PV is preferred and SolarEdge is used as fallback.
   - `test_charging_automation.py::TestKeepaliveAlwaysRuns` — verifies
     the keepalive refresh runs on every cycle (day and night).
   - `test_homeassistant.py` — per-EV sensor unit tests (Rivian, Tesla,
     idle=0W, unavailable=None), a live per-EV sensor test, and a live
     PV sensor test, all gated by `HA_LIVE=1`.

## Conventions

- Python 3, unittest (not pytest), tests under charging_automation/tests/.
- Tests insert the parent dir on sys.path so they run from repo root.
- Live tests are gated by env vars (HA_LIVE=1, SOLAREDGE_LIVE=1) and read
  /app/config.json.
- Logging via module-level `logger = logging.getLogger(__name__)`.
- Config is a plain class reading /app/config.json with required keys
  accessed via data['key'] (KeyError on missing) and optional via .get().
