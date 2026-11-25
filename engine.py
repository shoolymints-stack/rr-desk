"""
Round Robin Desk: the math. No network, no database, so every rule here can be tested offline.

Same method as MINTZ:
  fair price = Pinnacle's no-vig line, or the median no-vig line of 3+ books at the SAME number
  edge       = fair win chance minus the win chance the offered price needs
  grade      = A at 3.0%+, A+ at 3.3%+   (this desk only plays A+)
  1R         = the stake. A ticket's R = net result / total ticket stake.
"""
import itertools
import math
import statistics

MIN_EDGE = 0.033        # 3.3% or better, per leg
APLUS_EDGE = 0.033
A_EDGE = 0.030
MIN_ODDS = -120         # no leg priced worse than this (MINTZ props use +100; game lines live near -110)
MAX_ODDS = 300          # no long shots inside a parlay
LEGS = 4                # 4-team round robin
BY = 2                  # by 2s = 6 two-team parlays
GUARD_FAIR_GAP = 0.10   # a price this far off the fair line is almost always a mislabeled market
GUARD_BOOK_GAP = 0.08   # ...and so is a price this far off the other books
SHARP = "Pinnacle"
EXCHANGES = {"Kalshi", "Polymarket", "Polymarket US", "Novig", "ProphetX"}   # no round robins there: fair price only


def imp(o):
    """win chance an American price needs to break even"""
    o = float(o)
    return 100 / (o + 100) if o > 0 else -o / (-o + 100)


def dec(o):
    """American price -> decimal payout (stake included)"""
    o = float(o)
    return 1 + (o / 100 if o > 0 else 100 / -o)


def grade(edge):
    return "A+" if edge >= APLUS_EDGE else "A" if edge >= A_EDGE else "PASS"


def _label(market, side, point, home, away):
    if market == "h2h":
        return f"{home if side == 'home' else away} to win"
    if market == "spreads":
        p = point if side == "home" else -point
        return f"{home if side == 'home' else away} {p:+g}"
    return f"{'Over' if side == 'over' else 'Under'} {point:g}"


def event_markets(ev, book_names):
    """{(market, point): {book: {sideA: price, sideB: price}}}. Spreads are keyed by the HOME team's number."""
    home, away = ev["home_team"], ev["away_team"]
    grp = {}
    for b in ev.get("bookmakers", []):
        bk = book_names.get(b["key"], b.get("title") or b["key"])
        for m in b.get("markets", []):
            k = m["key"]
            if k not in ("h2h", "spreads", "totals"):
                continue
            sides, point = {}, None
            for o in m.get("outcomes", []):
                if k == "totals":
                    sd = o["name"].lower()
                    if sd not in ("over", "under") or o.get("point") is None:
                        continue
                    point = float(o["point"])
                else:
                    sd = "home" if o["name"] == home else "away" if o["name"] == away else None
                    if sd is None:
                        continue
                    if k == "spreads":
                        if o.get("point") is None:
                            continue
                        if sd == "home":
                            point = float(o["point"])
                sides[sd] = o["price"]
            if len(sides) != 2 or (k != "h2h" and point is None):
                continue
            grp.setdefault((k, point), {})[bk] = sides
    return grp


def fair_for(books):
    """no-vig chance of the first side. Pinnacle if it's there, else the median of 3+ books. None = not enough to judge."""
    a, b = sorted(next(iter(books.values())))  # ('away','home') or ('over','under')
    nv = {bk: imp(p[a]) / (imp(p[a]) + imp(p[b])) for bk, p in books.items()}
    if SHARP in nv:
        return a, b, nv[SHARP], SHARP
    if len(nv) >= 3:
        return a, b, statistics.median(nv.values()), f"{len(nv)}-book consensus"
    return a, b, None, None


def scan_event(ev, book_names, play_books, min_edge=MIN_EDGE):
    """every leg in one game that clears the edge at a book where a round robin can be placed"""
    home, away = ev["home_team"], ev["away_team"]
    out = []
    for (market, point), books in event_markets(ev, book_names).items():
        a, b, fair_a, src = fair_for(books)
        if fair_a is None:
            continue
        for side, fair in ((a, fair_a), (b, 1 - fair_a)):
            for bk, p in books.items():
                if bk not in play_books or bk == SHARP or bk in EXCHANGES:
                    continue
                price = p[side]
                if not (MIN_ODDS <= price <= MAX_ODDS) or -100 < price < 100:
                    continue
                edge = fair - imp(price)
                if edge < min_edge or edge > GUARD_FAIR_GAP:
                    continue
                others = [imp(q[side]) for ob, q in books.items() if ob != bk]
                if others and statistics.median(others) - imp(price) > GUARD_BOOK_GAP:
                    continue
                out.append({"event_id": ev["id"], "game": f"{away} @ {home}", "home": home, "away": away,
                            "commence": ev["commence_time"], "market": market, "side": side, "point": point,
                            "selection": _label(market, side, point, home, away), "book": bk, "odds": int(price),
                            "fair": round(fair, 4), "edge": round(edge, 4), "grade": grade(edge),
                            "fair_src": src, "n_books": len(books)})
    return out


def best_per_game(cands):
    best = {}
    for c in cands:
        if c["event_id"] not in best or c["edge"] > best[c["event_id"]]["edge"]:
            best[c["event_id"]] = c
    return sorted(best.values(), key=lambda c: -c["edge"])


def parlay_ev(legs, by=BY):
    """expected return per $1 staked across the round robin, at the fair chances (legs are different games)"""
    combos = list(itertools.combinations(legs, by))
    tot = sum(math.prod(l["fair"] * dec(l["odds"]) for l in c) - 1 for c in combos)
    return tot / len(combos)


def build_ticket(cands, legs=LEGS, by=BY):
    """ONE round robin: 4 legs, 4 different games, all priced at the same book (a ticket can't mix books).
    Fewer than 4 qualifying legs at any one book = no ticket today."""
    by_book = {}
    for c in cands:
        by_book.setdefault(c["book"], []).append(c)
    best = None
    for bk, cs in by_book.items():
        top = best_per_game(cs)[:legs]
        if len(top) < legs:
            continue
        ev = parlay_ev(top, by)
        if best is None or ev > best["ev"]:
            best = {"book": bk, "legs": top, "ev": round(ev, 4), "by": by,
                    "parlays": [{"legs": [top.index(l) for l in c], "dec": round(math.prod(dec(l["odds"]) for l in c), 4)}
                                for c in itertools.combinations(top, by)]}
    return best


def settle_leg(market, side, point, home_score, away_score):
    if market == "h2h":
        d = home_score - away_score
        if d == 0:
            return "Push"
        return "Won" if (d > 0) == (side == "home") else "Lost"
    if market == "spreads":
        d = home_score + point - away_score
        if side == "away":
            d = -d
    else:
        d = home_score + away_score - point
        if side == "under":
            d = -d
    return "Won" if d > 0 else "Lost" if d < 0 else "Push"


def settle_ticket(results, odds, unit=1.0, by=BY):
    """results: Won / Lost / Push / Void per leg. A pushed or void leg drops out of its parlays (standard book rule).
    Returns (net, stake, R). None while any leg is still pending."""
    if any(r not in ("Won", "Lost", "Push", "Void") for r in results):
        return None
    net, stake = 0.0, 0.0
    for c in itertools.combinations(range(len(results)), by):
        stake += unit
        if any(results[i] == "Lost" for i in c):
            net -= unit
        else:
            net += unit * (math.prod(dec(odds[i]) for i in c if results[i] == "Won") - 1)
    return round(net, 2), round(stake, 2), round(net / stake, 3)


def r_stats(rs):
    """Tharp stats on a list of R-multiples"""
    n = len(rs)
    if not n:
        return {"n": 0, "total_r": 0, "exp_r": None, "win_rate": None, "sqn": None, "best": None, "worst": None}
    mean = sum(rs) / n
    sd = statistics.pstdev(rs) if n > 1 else 0
    return {"n": n, "total_r": round(sum(rs), 2), "exp_r": round(mean, 3),
            "win_rate": round(100 * sum(r > 0 for r in rs) / n, 1),
            "sqn": round(math.sqrt(min(n, 100)) * mean / sd, 2) if sd else None,
            "best": round(max(rs), 2), "worst": round(min(rs), 2)}
