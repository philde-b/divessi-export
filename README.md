# DiveSSI export

![license](https://img.shields.io/github/license/philde-b/divessi-export.svg)

Back up everything the MySSI app will hand over. The script talks to the same
endpoint the app uses, stores the untouched JSON for every command that returns
data, derives CSV tables from it, optionally creates a native Subsurface
logbook, and downloads any media the payloads refer to.

Nothing is filtered on the way in. A backup that keeps only a curated subset of
fields is not a backup, so the raw response is always written first and every
CSV is generated from that. Verified against the live API in August 2026.

## Install

```sh
git clone https://github.com/philde-b/divessi-export.git
cd divessi-export
python -m venv venv
venv/bin/pip install -r requirements.txt        # Windows: venv\Scripts\pip
```

Python 3.10 or newer; only `requests` is needed at runtime.

Credentials can come from the environment, which keeps the password off disk:

```sh
export SSI_USERNAME="you@example.com"
export SSI_PASSWORD="..."
```

or from a plain text file with the email on the first line and the password on
the second, passed with `--credentials FILE`, or, as a fallback, from a local
Python file that is gitignored:

```sh
cp params.example.py params.py
```

## Usage

The known commands (logbook, profile, dive sites), with every field kept:

```sh
python export.py
```

Everything the account exposes, probing a list of likely commands:

```sh
python export.py --discover --out backup/2026-08-21
```

Specific commands only:

```sh
python export.py --only get_divelog,get_profile
```

Export and create a Subsurface logbook in one run:

```sh
python export.py --subsurface
```

Options:

| flag | effect |
| --- | --- |
| `--out DIR` | output directory, default `export` |
| `--credentials FILE` | read email (line 1) and password (line 2) from FILE |
| `--discover` | probe every candidate in `divessi/constants.py` instead of only `get_divelog` |
| `--only a,b` | fetch exactly these commands |
| `--delay S` | seconds between requests, default 2 |
| `--cooldown S` | one-time wait when the server goes silent, default 300 |
| `--subsurface` | also write `subsurface.ssrf` for direct import into Subsurface |
| `--no-media` | skip downloading referenced media |
| `--media-any-host` | also download media hosted outside divessi.com |
| `-v` | verbose logging |

## Output

```
export/
  manifest.json                 what was probed, what returned data, counts
  divelog.csv                   curated logbook, compatible with the old script
  subsurface.ssrf               optional native Subsurface logbook
  raw/<command>.json            untouched server response, one per command
  csv/<command>__<table>.csv    one CSV per list of objects in each response
  media/<hash>_<name>           photos, cards, PDFs referenced by the data
```

CSV files are UTF-8 with BOM so Excel opens them with accents intact. Nested
keys are joined with dots, lists of objects are expanded with an index, and
every row carries the union of all columns so ragged data never drops a field.

## Subsurface conversion

Use `--subsurface` to generate `export/subsurface.ssrf`. In Subsurface, choose
**File > Import log files** and select that file. The converter maps dive
numbers, dates, times, durations, maximum and average depths, temperatures,
ratings, dive sites and GPS coordinates, buddies, dive leaders, cylinders,
gas mixes, pressures, weights, notes and other supported fields.

Every non-empty SSI field is also retained as `SSI:` extradata. This makes the
`.ssrf` useful without throwing away information that Subsurface has no direct
field for. MySSI summary-only dives do not contain a time-series depth profile,
so the converter deliberately does not fabricate one.

An existing raw backup can be converted without contacting SSI:

```sh
python -m divessi.subsurface export/raw/get_divelog.json my-dives.ssrf
```

## Known commands

The API is a single dispatcher driven by a `what` parameter and has no public
documentation. These are confirmed against the live server:

| command | returns |
| --- | --- |
| `authenticate` | the session token |
| `get_divelog` | `logbook_details`, `logbook_sites`, `logbook_buddies`, `logbook_stats`, `logbook_history`, `verified_dives`, `homescreen_dives`, freediving sessions, highest certified dive numbers |
| `get_user_data` | the profile: user id, leader number, facility, name, language, country, ... |
| `get_divesites` | dive site catalogue |

Every run fetches all of them. `--discover` additionally probes the guesses in
`DISCOVERY_CANDIDATES` (`divessi/constants.py`); an unknown name is a fast 404
and is skipped. If you find a command that returns data, add it to
`KNOWN_COMMANDS` and open a pull request.

To learn the real command names, capture the app's traffic with a proxy such as
mitmproxy and look at the `what` values it sends.

## Rate limiting

The server does not send 429s. After a run of requests it simply stops
answering, for well over five minutes, and the allowance looks like a rolling
window: a fresh run started shortly after a previous one got three calls in
before going quiet. The client therefore spaces calls by `--delay` (default
2 s), caps each probe at 15 s, never retries a connect or read failure, and
after two silent calls in a row backs off once for `--cooldown` seconds
(default 300) and retries from the start of the streak. If the silence
continues, the remaining commands are marked `skipped` in the manifest and
the run prints a ready-made `--only ...` line to fetch them later. Everything
already fetched, manifest included, is on disk by then.

For a first backup, run without `--discover`: the known commands are three
calls and complete in seconds. Use `--discover` on its own, later, when the
quota has had time to recover.

## Library use

```python
from divessi.api import SSIApi

api = SSIApi(username, password)
raw = api.get_raw("get_divelog")      # untouched JSON
rows = api.get_divelog()              # curated list of dicts
```

`get_divelog()` no longer raises on unmapped tank types, unknown dive sites,
missing fields or an empty logbook.

## A note on the API

Credentials are sent as URL query parameters. That is how the server is built
and not something this client can change. HTTPS protects them in transit, but
query strings tend to end up in server logs, so use a password for SSI that you
use nowhere else.

## Tests

```sh
make tests
```

## Motivations

- The MySSI website shows less than the app holds, and its CSV export drops
  most fields.
- Your dive data is yours. You should be able to keep a local copy and move it
  to any other logbook.
