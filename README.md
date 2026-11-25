# Parlay Desk (repo: rr-desk)

NCAA men's basketball. **Paper only.** It never places a bet.

Each day at 11:00 AM ET it scans the slate and keeps legs with a **3.3% or better edge** over the fair price
(Pinnacle's no-vig line, or the median of 3+ books at the same number: the MINTZ method). If 3 legs from 3 different
games qualify **at one book**, it locks a single 3-team parlay. Fewer than 3 means no ticket that day. Every
qualifying leg is also tracked as a single.

Nov 25, 2026: changed from a 4-team round robin by 2s to one 3-team parlay (`LEGS` and `BY` in `engine.py`).

Graded from final scores. The record is in R (1R = the stake): expectancy, SQN, hit rate vs break-even, and
closing-line value.

## Run it

No packages to install. Needs Python 3.10+.

```
export ODDS_API_KEY=your_key_from_the-odds-api.com
python3 server.py
```

Open http://localhost:8000.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `ODDS_API_KEY` | none | The Odds API key (the same one MINTZ uses works) |
| `RR_DB` | `rr.db` next to server.py | Where the record lives. On Railway point it at a volume (`/data/rr.db`) or the record resets on every deploy |
| `LOCK_HOUR` | `11` | Hour (ET) the day locks |
| `PARLAY_UNIT` | `5` | Paper dollars per ticket |
| `BANKROLL` | `500` | Paper starting bankroll |
| `ADMIN_KEY` | none | Needed for `POST /api/lock` and `POST /api/grade` (header `x-admin-key`). Unset = those are off |

Rules live at the top of `engine.py` (`MIN_EDGE`, `MIN_ODDS`, `MAX_ODDS`).

## Odds API credits

About 3 credits per slate scan and 2 per score check: roughly 30-60 a day during the season, shared with whatever else uses the key.

## Tests

```
python3 tests/test_engine.py
python3 tests/test_server.py
```
