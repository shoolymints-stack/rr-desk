"""End to end with a fake odds feed: lock -> closing line -> grade -> record -> pages. No network."""
import json, os, sys, tempfile, threading, urllib.request
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["RR_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["ODDS_API_KEY"] = "test"; os.environ["ADMIN_KEY"] = "k"
import server as S

def game(i, soft, tip):
    o = lambda h, a, p: [{"name": f"H{i}", "price": h, "point": p}, {"name": f"A{i}", "price": a, "point": -p}]
    return {"id": f"e{i}", "commence_time": tip.isoformat(timespec="seconds").replace("+00:00", "Z"), "home_team": f"H{i}", "away_team": f"A{i}",
            "bookmakers": [{"key": "pinnacle", "markets": [{"key": "spreads", "outcomes": o(-105, -105, -3.5)}]},
                           {"key": "draftkings", "markets": [{"key": "spreads", "outcomes": o(soft, -140, -3.5)}]}]}

now = datetime.now(timezone.utc)
FEED = {"odds": [game(i, 116, now + timedelta(minutes=8)) for i in range(4)] + [game(4, -110, now + timedelta(minutes=8))], "scores": []}
S.http = lambda url, params: FEED["scores"] if url.endswith("/scores") else FEED["odds"]
S.todays_events = lambda evs: evs      # the test can run at any hour
S.init_db()

t = S.today(); assert t["preview"] and t["ticket"] and len(t["singles"]) == 4, t
assert S.record()["tickets"] == []                      # a preview records nothing
r = S.lock_day(); assert r == {"locked": True, "ticket": True, "singles": 4}, r
assert S.lock_day()["locked"] is False                  # once a day
t = S.today(); assert t["locked_at"] and len(t["ticket"]["legs"]) == 4 and len(t["ticket"]["parlays"]) == 6 and t["ticket"]["stake"] == 6

# closing line: Pinnacle moves our side to -125/+105 -> we beat the close
for g in FEED["odds"][:4]:
    g["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = -125; g["bookmakers"][0]["markets"][0]["outcomes"][1]["price"] = 105
S.CACHE["ts"] = 0; S.capture_close()
rec = S.record(); assert rec["clv"]["n"] == 4 and rec["clv"]["beat"] == 4 and rec["clv"]["avg"] > 5, rec["clv"]

# games end: 3 legs cover, 1 doesn't. Move tip times back so grading is due.
with S.db() as c: c.execute("update legs set commence=?", ((now - timedelta(hours=3)).isoformat(),))
FEED["scores"] = [{"id": f"e{i}", "completed": True, "scores": [{"name": f"H{i}", "score": "80" if i < 3 else "70"}, {"name": f"A{i}", "score": "70"}]} for i in range(4)]
S.grade_all()
rec = S.record()
tk = rec["tickets"][0]; exp = round(3 * (2.16 * 2.16 - 1) - 3, 2)
assert tk["result"] == "Won" and tk["net"] == exp and abs(tk["r"] - exp / 6) < 1e-3, tk
assert rec["single_stats"]["n"] == 4 and rec["single_stats"]["hit_rate"] == 75.0 and rec["ticket_stats"]["bankroll"] == round(500 + exp, 2)

srv = S.ThreadingHTTPServer(("127.0.0.1", 0), S.H); threading.Thread(target=srv.serve_forever, daemon=True).start()
u = f"http://127.0.0.1:{srv.server_port}"
get = lambda p: urllib.request.urlopen(u + p).read()
assert b"Round Robin" in get("/") and json.loads(get("/api/today"))["ticket"]["result"] == "Won" and json.loads(get("/api/health"))["ok"]
try: urllib.request.urlopen(urllib.request.Request(u + "/api/lock", method="POST")); raise SystemExit("lock should need the admin key")
except urllib.error.HTTPError as e: assert e.code == 403
print("ok end to end: ticket", tk["net"], f"{tk['r']}R", "| singles", rec["single_stats"], "| clv", rec["clv"])
