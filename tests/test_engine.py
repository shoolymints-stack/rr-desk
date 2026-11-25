import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E

BK = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "pinnacle": "Pinnacle", "novig": "Novig"}
PLAY = {"DraftKings", "FanDuel", "BetMGM"}

def mk(key, a, b, point=None, home="Home", away="Away"):
    if key == "totals":
        return {"key": key, "outcomes": [{"name": "Over", "price": a, "point": point}, {"name": "Under", "price": b, "point": point}]}
    o = [{"name": home, "price": a}, {"name": away, "price": b}]
    if key == "spreads": o[0]["point"], o[1]["point"] = point, -point
    return {"key": key, "outcomes": o}

def ev(i, books):
    return {"id": f"e{i}", "commence_time": "2026-11-25T00:00:00Z", "home_team": "Home", "away_team": "Away",
            "bookmakers": [{"key": k, "markets": m} for k, m in books.items()]}

def test_prices():
    assert abs(E.imp(-110) - 0.5238) < 1e-4 and abs(E.imp(150) - 0.4) < 1e-9
    assert abs(E.dec(-110) - 1.9091) < 1e-4 and E.dec(200) == 3.0

def test_edge_vs_pinnacle():
    # Pinnacle -105/-105 = fair 50%. DraftKings hangs +108 on the home spread: needs 48.08% -> edge 1.92% (not enough)
    e = ev(1, {"pinnacle": [mk("spreads", -105, -105, -3.5)], "draftkings": [mk("spreads", 108, -130, -3.5)]})
    assert E.scan_event(e, BK, PLAY) == []
    # +116 needs 46.3% -> edge 3.7% -> A+
    e = ev(1, {"pinnacle": [mk("spreads", -105, -105, -3.5)], "draftkings": [mk("spreads", 116, -140, -3.5)]})
    c = E.scan_event(e, BK, PLAY)
    assert len(c) == 1 and c[0]["book"] == "DraftKings" and c[0]["grade"] == "A+" and abs(c[0]["edge"] - 0.037) < 1e-3
    assert c[0]["selection"] == "Home -3.5"

def test_fair_needs_same_number_and_3_books():
    # only 2 books and no Pinnacle: can't judge
    e = ev(1, {"draftkings": [mk("totals", 120, -150, 140.5)], "fanduel": [mk("totals", -110, -110, 140.5)]})
    assert E.scan_event(e, BK, PLAY) == []
    # a different number at Pinnacle is not a comparison
    e = ev(1, {"pinnacle": [mk("totals", -105, -105, 141.5)], "draftkings": [mk("totals", 120, -150, 140.5)]})
    assert E.scan_event(e, BK, PLAY) == []

def test_guards_and_ranges():
    # mislabeled market: +250 where fair is 50% is a data error, not an edge
    e = ev(1, {"pinnacle": [mk("h2h", -105, -105)], "draftkings": [mk("h2h", 250, -300)]})
    assert E.scan_event(e, BK, PLAY) == []
    # exchange and Pinnacle prices are never played, even with an edge
    e = ev(1, {"pinnacle": [mk("h2h", -105, -105)], "novig": [mk("h2h", 116, -130)]})
    assert E.scan_event(e, BK, PLAY | {"Novig"}) == []

def four_games(book="draftkings"):
    return [ev(i, {"pinnacle": [mk("spreads", -105, -105, -3.5)], book: [mk("spreads", 116, -140, -3.5)]}) for i in range(4)]

def test_ticket_needs_3_at_one_book():
    c = sum((E.scan_event(e, BK, PLAY) for e in four_games()), [])
    t = E.build_ticket(c)
    assert t and t["book"] == "DraftKings" and len(t["legs"]) == 3 and len(t["parlays"]) == 1
    assert len({l["event_id"] for l in t["legs"]}) == 3
    assert abs(t["ev"] - ((0.5 * 2.16) ** 3 - 1)) < 1e-3           # +26% per $1 with a real 3.7% edge on each leg
    assert abs(t["parlays"][0]["dec"] - 2.16 ** 3) < 1e-3
    # 2 legs at one book and 1 at another = no ticket
    evs = four_games()[:2] + [ev(9, {"pinnacle": [mk("spreads", -105, -105, -3.5)], "fanduel": [mk("spreads", 116, -140, -3.5)]})]
    assert E.build_ticket(sum((E.scan_event(e, BK, PLAY) for e in evs), [])) is None

def test_settle_leg():
    assert E.settle_leg("spreads", "home", -3.5, 70, 66) == "Won" and E.settle_leg("spreads", "away", -3.5, 70, 66) == "Lost"
    assert E.settle_leg("spreads", "home", -4, 70, 66) == "Push"
    assert E.settle_leg("totals", "over", 140.5, 70, 66) == "Lost" and E.settle_leg("totals", "under", 140.5, 70, 66) == "Won"
    assert E.settle_leg("h2h", "away", None, 70, 66) == "Lost"

def test_settle_ticket():
    o = [-110] * 3
    assert E.settle_ticket(["Won"] * 3, o, 5) == (29.79, 5.0, 5.958)      # 6.96x on $5
    assert E.settle_ticket(["Won", "Won", "Lost"], o, 5) == (-5.0, 5.0, -1.0)   # any loss loses the ticket
    assert E.settle_ticket(["Lost", "Lost", "Lost"], o, 5) == (-5.0, 5.0, -1.0)
    assert E.settle_ticket(["Won", "Pending", "Lost"], o, 5) is None
    # a push drops that leg: the ticket pays as a 2-team parlay
    assert E.settle_ticket(["Won", "Won", "Push"], o, 5) == (13.22, 5.0, 2.645)
    assert E.settle_ticket(["Push", "Push", "Void"], o, 5) == (0.0, 5.0, 0.0)
    # the old round robin math still works when asked for by 2s
    assert E.settle_ticket(["Won", "Won", "Won", "Lost"], [-110] * 4, 1, by=2) == (4.93, 6.0, 0.822)

def test_r_stats():
    s = E.r_stats([2.645, -1, -0.393, 0.822])
    assert s["n"] == 4 and abs(s["exp_r"] - 0.5185) < 1e-3 and s["win_rate"] == 50.0
    assert E.r_stats([])["exp_r"] is None

if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("ok", k)
