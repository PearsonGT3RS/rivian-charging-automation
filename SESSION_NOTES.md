# Charging Automation — Session Notes

Last updated: 2026-11-09 (session 3, continued)
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
  returns None.
- HomeAssistantAPI.py — Emporia load source; returns None on
  unavailable/unknown/non-numeric/HTTP error so LoadUnavailableError fires.
- ChargingAutomation.py — wires load_provider from HA + emporia_load_entities,
  catches LoadUnavailableError, uses power_flow.grid.
- Config.py — exposes solaredge_client_id/secret/site_id/redirect_uri,
  homeassistant_url/token, emporia_load_entities, night_time_start/end.
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
Result: 108 tests, OK (3 skipped — Tesla suite skipped when
tesla-fleet-api / cryptography are not installed; live tests gated by
HA_LIVE / SOLAREDGE_LIVE / TESLA_LIVE / RIVIAN_LIVE).

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

## Solar allocation logic (as of session 3)

`run_charging_automation()` computes the total solar budget for the EVs as:

    solar_for_ev = current_power + available_power

where:
- `available_power = -power_flow.grid` (positive = surplus / export).
- `current_power` = sum of each connected vehicle's estimated draw, where
  the estimate is:
    - `live_amps * VOLTS` when `is_charging() == True` (authoritative), OR
    - `_last_commanded[name].amps * VOLTS` when `is_charging() == False`
      but a command was issued within `COMMANDED_AMPS_TTL_SECONDS`
      (20 minutes), OR
    - `live_amps * VOLTS` as a last-resort fallback (only reached when
      `is_charging()` is False and no recent command is cached — in
      practice this yields 0 because `live_amps` is 0 when the car is
      asleep/stopped).

  This is important because Emporia's `load` already includes whatever the
  EV is drawing, so we must add that draw back to the surplus to get the
  total budget. The `_last_commanded` cache (populated by `apply_charging()`)
  handles the wake/ramp window where `is_charging()` transiently reports
  False even though the car is drawing power. The 20-minute TTL bounds the
  window where a stale command could over-count; since daytime cycles run
  every 15 minutes, a stale command is corrected within one cycle.
  `apply_charging()` clears the cache entry when it commands a stop
  (`target_amps < AMPS_MIN`), so a finished session does not leave phantom
  watts behind.

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

The commanded-amps changes (363e447, 4b480b4) only affect the *input* to
the split (`solar_for_ev`); the split ratios and minimum-watt reassignment
are unchanged and remain correct.

### Why 363e447 alone was insufficient

The 363e447 gate (`is_charging()` only) fixed the phantom-watt case but
reintroduced the original under-count during the wake/ramp window. Observed
in production on 2026-11-09:

    Power flow: pv=6331W load=5767W grid=-564W
    Surplus available: 564W
    Current EV Draw: 0W | Total Solar for EVs: 564W
    Tesla: target 564W → 2A (Currently Charging: False)

The Tesla was drawing power (load=5767W includes it) but reported
`charging_state != 'Charging'`, so `current_power` was 0 and we allocated
only the new 564W surplus instead of (existing draw + 564W). 4b480b4 fixes
this by trusting the last commanded amps for up to 20 minutes after the
command, regardless of `is_charging()`.

## Known gaps / follow-ups

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
7. Consider adding an end-to-end daytime cycle test for
   run_charging_automation() with all APIs mocked (solar allocation +
   apply_charging path).
8. EV-draw wake/ramp edge case — ADDRESSED in 4b480b4 via the
   `_last_commanded` cache (20-minute TTL). Remaining caveats:
   - `_last_commanded` is module-level state and resets on process restart.
     The first cycle after a restart falls back to `is_charging()`; if that
     cycle lands in the wake/ramp window, we under-count once and
     self-correct on the next cycle.
   - If a vehicle is commanded to charge but the user unplugs it before the
     next cycle, the cache will over-count for up to 20 minutes. The
     `is_charger_connected()` check at the top of the cycle mitigates this
     (the vehicle is dropped from `current_power` entirely when
     disconnected), but a mid-cycle unplug is not detected until the next
     poll.
   - Consider persisting `_last_commanded` to disk (e.g. alongside the
     session files) if restart-induced under-counts prove disruptive.

## Conventions

- Python 3, unittest (not pytest), tests under charging_automation/tests/.
- Tests insert the parent dir on sys.path so they run from repo root.
- Live tests are gated by env vars (HA_LIVE=1, SOLAREDGE_LIVE=1) and read
  /app/config.json.
- Logging via module-level `logger = logging.getLogger(__name__)`.
- Config is a plain class reading /app/config.json with required keys
  accessed via data['key'] (KeyError on missing) and optional via .get().
