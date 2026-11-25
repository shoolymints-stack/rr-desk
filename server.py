"""
Parlay Desk: NCAA men's basketball. PAPER ONLY. Nothing here places a bet.

Each day at LOCK_HOUR ET it scans the slate, keeps legs with a 3.3%+ edge over the fair price, and if 3 legs
from 3 different games qualify at ONE book it locks a single 3-team parlay. Fewer than 3 = no ticket.
Every qualifying leg is also tracked as a single so the edge can be judged faster than tickets alone allow.
Graded from final scores. Record is kept in R (1R = the stake), with closing-line value.

No packages to install: python3 server.py
"""
import json, os, sqlite3, threading, time, traceback
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import engine

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("RR_DB", os.path.join(HERE, "rr.db"))      # on Railway point this at a volume, e.g. /data/rr.db
KEY = os.environ.get("ODDS_API_KEY", "")
ODDS = "https://api.the-odds-api.com/v4"
SPORT = "basketball_ncaab"
ET = ZoneInfo("America/New_York")
LOCK_HOUR = int(os.environ.get("LOCK_HOUR", "11"))             # 11:00 AM ET
UNIT = float(os.environ.get("PARLAY_UNIT", "5"))               # paper $ per ticket (1% of the $500 bankroll)
BANKROLL = float(os.environ.get("BANKROLL", "500"))
MAX_SINGLES = 8                                                # tracked single legs per day
BOOK_KEYS = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "williamhill_us": "Caesars",
             "fanatics": "Fanatics", "espnbet": "ESPN BET", "betrivers": "BetRivers", "pinnacle": "Pinnacle",
             "novig": "Novig"}                                 # <=10 books = 1 region on the Odds API
PLAY_BOOKS = {"DraftKings", "FanDuel", "BetMGM", "Caesars", "Fanatics", "ESPN BET", "BetRivers"}   # books with parlays
CREDITS = {"remaining": None, "used": None}
CACHE = {"ts": 0, "events": [], "err": None}
MUTEX = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    with db() as c:
        c.execute("""create table if not exists legs(id integer primary key, day text, locked_at text, event_id text, game text,
          home text, away text, commence text, market text, side text, point real, selection text, book text, odds integer,
          fair real, edge real, grade text, fair_src text, ticket_id integer, single integer default 0,
          result text default 'Pending', score text, graded_at text, close_fair real, clv real, close_ts text)""")
        c.execute("""create table if not exists tickets(id integer primary key, day text, locked_at text, book text, unit real,
          stake real, ev real, result text default 'Pending', net real, r real, graded_at text)""")
        c.execute("create table if not exists meta(k text primary key, v text)")


def http(url, params):
    url += "?" + urlencode(params)
    with urlopen(Request(url, headers={"User-Agent": "ParlayDesk", "Accept": "application/json"}), timeout=40) as r:
        h = r.headers
        if h.get("x-requests-remaining") is not None:
            CREDITS["remaining"], CREDITS["used"] = h.get("x-requests-remaining"), h.get("x-requests-used")
        return json.loads(r.read())


def fetch_odds(max_age=900):
    """the whole NCAAB slate in one call (3 credits). Cached 15 minutes so page views don't spend credits."""
    if not KEY:
        CACHE["err"] = "No ODDS_API_KEY set yet."
        return []
    if time.time() - CACHE["ts"] < max_age:
        return CACHE["events"]
    try:
        CACHE["events"] = http(f"{ODDS}/sports/{SPORT}/odds", {"apiKey": KEY, "markets": "h2h,spreads,totals",
                               "oddsFormat": "american", "bookmakers": ",".join(BOOK_KEYS)})
        CACHE["ts"], CACHE["err"] = time.time(), None
    except Exception as e:
        CACHE["err"] = f"Odds feed error: {e}"
    return CACHE["events"]


def et_day(dt=None):
    return (dt or datetime.now(ET)).strftime("%Y-%m-%d")


def todays_events(events):
    """games that tip between now and 3 AM ET tonight"""
    n = datetime.now(timezone.utc)
    end = (datetime.now(ET) + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
    return [e for e in events if n < datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) <= end]


def scan(max_age=900):
    cands = []
    for ev in todays_events(fetch_odds(max_age)):
        cands += engine.scan_event(ev, BOOK_KEYS, PLAY_BOOKS)
    return cands


def lock_day(force=False):
    """once a day: lock the ticket (if 3 legs qualify at one book) and the tracked singles"""
    day = et_day()
    with MUTEX:
        with db() as c:
            if c.execute("select 1 from meta where k=?", ("locked:" + day,)).fetchone() and not force:
                return {"locked": False, "why": "already locked today"}
        cands = scan(max_age=0)
        if CACHE["err"]:
            return {"locked": False, "why": CACHE["err"]}
        ticket = engine.build_ticket(cands)
        singles = engine.best_per_game(cands)[:MAX_SINGLES]
        ts = now()
        with db() as c:
            if force:
                c.execute("delete from legs where day=? and result='Pending'", (day,))
                c.execute("delete from tickets where day=? and result='Pending'", (day,))
            tid = None
            ins = """insert into legs(day,locked_at,event_id,game,home,away,commence,market,side,point,selection,book,odds,
                     fair,edge,grade,fair_src,ticket_id,single) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
            row = lambda l, t, s: (day, ts, l["event_id"], l["game"], l["home"], l["away"], l["commence"], l["market"], l["side"],
                                   l["point"], l["selection"], l["book"], l["odds"], l["fair"], l["edge"], l["grade"],
                                   l["fair_src"], t, s)
            if ticket:
                tid = c.execute("insert into tickets(day,locked_at,book,unit,stake,ev) values(?,?,?,?,?,?)",
                                (day, ts, ticket["book"], UNIT, UNIT * len(ticket["parlays"]), ticket["ev"])).lastrowid
                for l in ticket["legs"]:
                    c.execute(ins, row(l, tid, 0))
            for l in singles:
                c.execute(ins, row(l, None, 1))
            c.execute("insert or replace into meta values(?,?)", ("locked:" + day, ts))
        print(f"LOCK {day}: {len(cands)} qualifying prices, {len(singles)} singles, ticket={'yes at ' + ticket['book'] if ticket else 'none'}", flush=True)
        return {"locked": True, "ticket": bool(ticket), "singles": len(singles)}


def capture_close():
    """about 10 minutes before tip: closing fair price at the same number. CLV = closing fair chance minus what our price needed."""
    with db() as c:
        pend = [dict(r) for r in c.execute("select * from legs where result='Pending' and close_ts is null")]
    soon = [l for l in pend if 0 < (datetime.fromisoformat(l["commence"].replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds() <= 720]
    if not soon:
        return
    evs = {e["id"]: e for e in fetch_odds(max_age=300)}
    with db() as c:
        for l in soon:
            cf = None
            ev = evs.get(l["event_id"])
            if ev:
                books = engine.event_markets(ev, BOOK_KEYS).get((l["market"], l["point"]))
                if books:
                    a, b, fa, _ = engine.fair_for(books)
                    if fa is not None:
                        cf = fa if l["side"] == a else 1 - fa
            c.execute("update legs set close_fair=?, clv=?, close_ts=? where id=?",
                      (cf, None if cf is None else round(cf - engine.imp(l["odds"]), 4), now(), l["id"]))


def grade_all():
    with db() as c:
        pend = [dict(r) for r in c.execute("select * from legs where result='Pending'")]
    due = [l for l in pend if (datetime.now(timezone.utc) - datetime.fromisoformat(l["commence"].replace("Z", "+00:00"))).total_seconds() > 6300]
    if not due or not KEY:
        return
    scores = {s["id"]: s for s in http(f"{ODDS}/sports/{SPORT}/scores", {"apiKey": KEY, "daysFrom": 3})}
    with db() as c:
        for l in due:
            s = scores.get(l["event_id"])
            if not s or not s.get("completed") or not s.get("scores"):
                # no final after 3 days = postponed or cancelled
                if (datetime.now(timezone.utc) - datetime.fromisoformat(l["commence"].replace("Z", "+00:00"))).days >= 3:
                    c.execute("update legs set result='Void', graded_at=? where id=?", (now(), l["id"]))
                continue
            pts = {x["name"]: int(x["score"]) for x in s["scores"]}
            hs, as_ = pts.get(l["home"]), pts.get(l["away"])
            if hs is None or as_ is None:
                continue
            res = engine.settle_leg(l["market"], l["side"], l["point"], hs, as_)
            c.execute("update legs set result=?, score=?, graded_at=? where id=?", (res, f"{as_}-{hs}", now(), l["id"]))
        for t in [dict(r) for r in c.execute("select * from tickets where result='Pending'")]:
            legs = [dict(r) for r in c.execute("select result,odds from legs where ticket_id=? order by id", (t["id"],))]
            out = engine.settle_ticket([l["result"] for l in legs], [l["odds"] for l in legs], t["unit"])
            if out:
                net, stake, r = out
                c.execute("update tickets set result=?, net=?, r=?, graded_at=? where id=?",
                          ("Won" if net > 0 else "Lost" if net < 0 else "Push", net, r, now(), t["id"]))


def loop():
    last_grade = 0
    while True:
        try:
            n = datetime.now(ET)
            if KEY and n.hour >= LOCK_HOUR:
                with db() as c:
                    done = c.execute("select 1 from meta where k=?", ("locked:" + et_day(),)).fetchone()
                if not done:
                    lock_day()
            capture_close()
            if time.time() - last_grade > 1800:
                last_grade = time.time()
                grade_all()
        except Exception:
            traceback.print_exc()
        time.sleep(60)


# ---------- record ----------
def leg_r(l):
    if l["result"] == "Won":
        return engine.dec(l["odds"]) - 1
    if l["result"] == "Lost":
        return -1.0
    return None


def record():
    with db() as c:
        legs = [dict(r) for r in c.execute("select * from legs order by id desc")]
        tickets = [dict(r) for r in c.execute("select * from tickets order by id desc")]
    singles = [l for l in legs if l["single"]]
    for l in legs:
        r = leg_r(l)
        l["r"] = None if r is None else round(r, 2)
    srs = [l["r"] for l in singles if l["r"] is not None]
    trs = [t["r"] for t in tickets if t["r"] is not None]
    wl = [l for l in singles if l["result"] in ("Won", "Lost")]
    clv = [l["clv"] for l in singles if l["clv"] is not None]
    net = sum(t["net"] or 0 for t in tickets)
    for t in tickets:
        t["legs"] = [l for l in legs if l["ticket_id"] == t["id"]][::-1]
    return {"tickets": tickets, "singles": singles,
            "ticket_stats": {**engine.r_stats(trs), "net": round(net, 2), "bankroll": round(BANKROLL + net, 2),
                             "start": BANKROLL, "pending": sum(t["result"] == "Pending" for t in tickets)},
            "single_stats": {**engine.r_stats(srs),
                             "be_rate": round(100 * sum(engine.imp(l["odds"]) for l in wl) / len(wl), 1) if wl else None,
                             "hit_rate": round(100 * sum(l["result"] == "Won" for l in wl) / len(wl), 1) if wl else None,
                             "avg_edge": round(100 * sum(l["edge"] for l in wl) / len(wl), 2) if wl else None,
                             "pending": sum(l["result"] == "Pending" for l in singles)},
            "clv": {"n": len(clv), "beat": sum(x > 0 for x in clv), "avg": round(100 * sum(clv) / len(clv), 2) if clv else None}}


def today():
    day = et_day()
    with db() as c:
        locked = c.execute("select v from meta where k=?", ("locked:" + day,)).fetchone()
        t = c.execute("select * from tickets where day=? order by id desc limit 1", (day,)).fetchone()
        legs = [dict(r) for r in c.execute("select * from legs where day=? order by edge desc", (day,))]
    out = {"day": day, "locked_at": locked["v"] if locked else None, "lock_hour": LOCK_HOUR, "unit": UNIT, "legs": engine.LEGS,
           "has_key": bool(KEY), "credits": CREDITS, "as_of": now()}
    if locked:
        out["ticket"] = None
        if t:
            tl = [l for l in legs if l["ticket_id"] == t["id"]]
            out["ticket"] = {**dict(t), "legs": tl, "parlays": engine.build_ticket(tl)["parlays"] if len(tl) == engine.LEGS else []}
        out["singles"] = [l for l in legs if l["single"]]
    else:   # before the lock: live preview, nothing recorded
        cands = scan()
        out["preview"] = True
        out["ticket"] = engine.build_ticket(cands)
        out["singles"] = engine.best_per_game(cands)[:MAX_SINGLES]
        out["games"] = len(todays_events(CACHE["events"]))
    out["err"] = CACHE["err"]
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/today":
                self.send(today())
            elif p == "/api/record":
                self.send(record())
            elif p == "/api/health":
                self.send({"ok": True, "has_key": bool(KEY), "credits": CREDITS, "as_of": now()})
            elif p in ("/", "/index.html"):
                self.send(open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
            else:
                self.send({"error": "not found"}, code=404)
        except Exception as e:
            traceback.print_exc()
            self.send({"error": str(e)}, code=500)

    def do_POST(self):
        p = urlparse(self.path).path
        admin = os.environ.get("ADMIN_KEY")
        if not admin or self.headers.get("x-admin-key") != admin:
            return self.send({"error": "admin key required"}, code=403)
        if p == "/api/lock":
            self.send(lock_day(force=True))
        elif p == "/api/grade":
            grade_all()
            self.send({"ok": True})
        else:
            self.send({"error": "not found"}, code=404)


def main():
    init_db()
    threading.Thread(target=loop, daemon=True).start()
    port = int(os.environ.get("PORT", "8000"))
    print(f"Parlay Desk on :{port}  key={'set' if KEY else 'MISSING'}  db={DB}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()


if __name__ == "__main__":
    main()
