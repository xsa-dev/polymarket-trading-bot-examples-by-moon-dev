"""
================================================================================
🌙 MOON DEV's FLIP HARVESTER — RUN ANALYZER
================================================================================
Reads a paper (or live) run's CSVs and answers the only four questions that
decide whether this bot is worth another dollar. Raw CSVs won't tell you —
and the P&L column in particular tells you NOTHING for the first ~1,200
trades, because per-trade noise (~$13 SD) swamps a <=$0.77 edge.

    Q1  Does the gate even fire, and what's blocking it?
    Q2  Does the book EVER bid 0.62 during a flip?   <- the killer question
    Q3  q = P(dog won | we sold it). Harvest is +EV iff q < ask.
    Q4  What entry prices do we actually get? (the base edge, 3x the harvest)

Usage:   python analyze_run.py [--live]

Every number printed here comes from YOUR run's CSVs. Nothing is assumed,
nothing is simulated, and where the sample is too small to say anything this
says so instead of printing a number that looks like an answer.
Built by Moon Dev 🌙
================================================================================
"""

import os
import sys
import csv
from math import sqrt

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BOT_DIR, "data")

# 🌙 the reference numbers this run is being tested AGAINST. All inherited from
#    flip_harvester_IDEA.md — none of them were measured by this bot.
CLAIM_TOUCH = 0.713           # P(dog touches strike in the final minute), n=10,180
CLAIM_P_WIN_TOUCH = 0.5947    # conservative P(win|touch) = P(win)/P(touch)
CLAIM_ENTRY = 0.370           # avg ask in the live pocket, n=56
ASKS = (0.60, 0.62, 0.65)     # the ladder we care about; 0.5947 is breakeven

ANSI = {"red": "31", "green": "32", "yellow": "33", "cyan": "36"}


def c(text, color=None, bold=False):
    codes = (["1"] if bold else []) + ([ANSI[color]] if color in ANSI else [])
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)


def rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def num(r, k, default=None):
    """🌙 CSV cells are strings and many are legitimately blank."""
    v = (r.get(k) or "").strip()
    if v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def wilson(k, n, z=1.96):
    """🌙 Wilson interval — behaves at small n, where normal-approx CIs go
    outside [0,1] and quietly lie to you. Returns (lo, hi)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def n_needed(precision, z=1.96):
    """🌙 sample size for +/-precision at 95% on a worst-case proportion."""
    return int((z / precision) ** 2 * 0.25) + 1


def bar(frac, width=28):
    filled = int(round(frac * width))
    return "█" * filled + "·" * (width - filled)


def section(title):
    print()
    print(c("=" * 74, "cyan"))
    print(c(f"  {title}", "cyan", bold=True))
    print(c("=" * 74, "cyan"))


# ============================================================================
# Q1 — the funnel: is the gate firing, and what is blocking it?
# ============================================================================
def q1_funnel(ev):
    section("Q1  THE FUNNEL — does the gate fire, and what blocks it?")
    if not ev:
        print(c("  No eval rows yet. The bot writes one per window at rollover.", "yellow"))
        return
    counts = {}
    for r in ev:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    total = len(ev)
    print(f"  {total} windows observed"
          f"  ({ev[0]['timestamp'][:16]} → {ev[-1]['timestamp'][:16]})\n")
    for action, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        frac = n / total
        col = "green" if action == "FILLED" else None
        print(f"    {action:<16} {n:>5}  {frac*100:>5.1f}%  {c(bar(frac), col)}")
    filled = counts.get("FILLED", 0)
    print()
    if filled == 0:
        print(c("  ⚠️  Zero entries. Read the top skip reason above — that is your", "yellow"))
        print(c("      binding constraint, not the strategy.", "yellow"))
    else:
        days = max(1e-9, (len(ev) / 288.0))
        print(f"  → {filled} entries ≈ {filled/days:.1f}/day. The math assumed ~13/day at "
              f"$10;\n    fewer entries means proportionally longer to learn anything.")


# ============================================================================
# Q2 — THE KILLER: does the book ever bid our ask during a flip?
# ============================================================================
def q2_touch_and_book(tr):
    section("Q2  THE KILLER QUESTION — does the book ever BID 0.62 on a flip?")
    if not tr:
        print(c("  No trade rows yet.", "yellow"))
        return
    n = len(tr)
    touched = [r for r in tr if r.get("touched") == "1"]
    lo, hi = wilson(len(touched), n)
    print(f"  touch rate: {len(touched)}/{n} = {c(f'{len(touched)/n*100:.1f}%', bold=True)}"
          f"   95% CI [{lo*100:.1f}%, {hi*100:.1f}%]")
    print(f"  claimed:    {CLAIM_TOUCH*100:.1f}% (n=10,180, backtest, final-minute definition)")
    if lo <= CLAIM_TOUCH <= hi:
        print(c("  → consistent with the claim so far.", "green"))
    elif n >= 30:
        print(c("  → OUTSIDE the CI. Your touches differ from the backtest's.", "red"))
    print(c(f"  (need ~{n_needed(0.10)} entries for +/-10pts, ~{n_needed(0.05)} for +/-5pts)", "cyan"))

    bids = [num(r, "max_bid_after_touch") for r in touched]
    bids = [b for b in bids if b is not None]
    print()
    if not bids:
        print(c("  No max_bid_after_touch data yet — this is THE column. Without it", "yellow"))
        print(c("  nothing about the exit can be known.", "yellow"))
        return
    bids.sort()
    print(f"  peak bid after the touch, {len(bids)} touches:")
    print(f"    min {bids[0]:.2f}   p25 {bids[len(bids)//4]:.2f}   "
          f"median {bids[len(bids)//2]:.2f}   p75 {bids[3*len(bids)//4]:.2f}   max {bids[-1]:.2f}")
    print()
    print("  fraction of touches where the bid REACHED our ask (this is f, the fill rate):")
    for ask in ASKS:
        k = sum(1 for b in bids if b >= ask)
        f_lo, f_hi = wilson(k, len(bids))
        f = k / len(bids)
        col = "green" if f >= 0.5 else ("yellow" if f >= 0.2 else "red")
        print(f"    bid >= {ask:.2f}   {k:>4}/{len(bids)}  {c(f'{f*100:>5.1f}%', col, bold=True)}"
              f"  CI [{f_lo*100:.0f}%, {f_hi*100:.0f}%]   {c(bar(f), col)}")
    print()
    f62 = sum(1 for b in bids if b >= 0.62) / len(bids)
    if len(bids) < 30:
        print(c("  ⏳ too few touches to conclude anything yet.", "yellow"))
    elif f62 < 0.15:
        print(c("  ✋ THE IDEA IS PROBABLY DEAD. The book rarely bids 0.62 on a flip,", "red", bold=True))
        print(c("     so the harvest almost never fills and this degrades into the", "red"))
        print(c("     plain hold-to-resolution dog bot. Cheapest possible finding.", "red"))
    else:
        print(c(f"  ✅ the bid does reach 0.62 on {f62*100:.0f}% of touches — the exit is", "green", bold=True))
        print(c("     mechanically possible. Now it's all about q (Q3).", "green"))
    print(c("\n  ⚠️  CAVEAT: in PAPER mode a fill is assumed the moment the bid touches", "yellow"))
    print(c("     our ask. Live, you also have to be at the front of the queue at that", "yellow"))
    print(c("     price. So paper f is an UPPER BOUND on the real fill rate.", "yellow"))


# ============================================================================
# Q3 — q: are we selling the winners? (harvest is +EV iff q < ask)
# ============================================================================
def q3_adverse_selection(tr):
    section("Q3  ADVERSE SELECTION — q = P(dog won | we sold it). Need q < ask.")
    sold = [r for r in tr if (num(r, "shares_sold", 0) or 0) > 0
            and r["result"] in ("WIN", "LOSS") and (r.get("dog_won") or "") != ""]
    if not sold:
        print(c("  No resolved sells yet. This is the number the whole idea turns on,", "yellow"))
        print(c("  and it cannot be obtained from any backtest — only from live fills.", "yellow"))
        print(c(f"  Need ~{n_needed(0.04)} resolved sells to resolve q to +/-4pts (the headroom).", "cyan"))
        return
    n = len(sold)
    k = sum(1 for r in sold if r["dog_won"] == "1")
    q = k / n
    lo, hi = wilson(k, n)
    ask = num(sold[-1], "exit_ask", 0.62)
    print(f"  q = {k}/{n} = {c(f'{q*100:.1f}%', bold=True)}   95% CI [{lo*100:.1f}%, {hi*100:.1f}%]")
    print(f"  our ask: {ask:.2f}   |   unconditional P(win|touch) reference: {CLAIM_P_WIN_TOUCH*100:.1f}%")
    print()
    if n < 30:
        print(c(f"  ⏳ n={n}. Far too small — the CI spans {(hi-lo)*100:.0f} points against a", "yellow"))
        print(c(f"     headroom of ~2.5. Keep running. (~{n_needed(0.04)} needed.)", "yellow"))
    elif lo > ask:
        print(c("  ✋ HARVEST IS -EV. You are confidently selling positions that go on", "red", bold=True))
        print(c("     to win more often than the price you sell them at. The bot's", "red"))
        print(c("     adverse-selection halt should already have fired.", "red"))
    elif hi < ask:
        print(c("  ✅ HARVEST IS +EV. q is confidently below the ask — each fill is", "green", bold=True))
        print(c(f"     worth ~{(ask-q)*100:.1f}c/share over holding.", "green"))
    else:
        print(c(f"  🤷 INCONCLUSIVE. The CI straddles the ask ({lo*100:.1f}% – {hi*100:.1f}% vs "
                f"{ask*100:.0f}c).", "yellow"))
        print(c(f"     This is the expected state until ~{n_needed(0.04)} sells. Keep going.", "yellow"))


# ============================================================================
# Q4 — the base edge: what entries do we ACTUALLY get?
# ============================================================================
def q4_entry_edge(tr):
    section("Q4  THE BASE EDGE — the entry price, worth 3x the harvest")
    resolved = [r for r in tr if r["result"] in ("WIN", "LOSS") and (r.get("dog_won") or "") != ""]
    entries = [num(r, "entry_fill_price") for r in tr]
    entries = [e for e in entries if e is not None]
    if not entries:
        print(c("  No entries yet.", "yellow"))
        return
    avg = sum(entries) / len(entries)
    print(f"  realized entry price: mean {c(f'{avg:.3f}', bold=True)} over {len(entries)} fills"
          f"   (assumed in the EV math: {CLAIM_ENTRY:.3f})")
    if avg > CLAIM_ENTRY + 0.02:
        print(c(f"  ⚠️  you are paying {(avg-CLAIM_ENTRY)*100:.1f}c more than the math assumed.", "yellow"))
        print(c("      The base edge shrinks ~1c/share for every 1c of extra entry.", "yellow"))
    print()
    if not resolved:
        print(c("  No resolved trades yet — cannot measure the dog win rate.", "yellow"))
        return
    n = len(resolved)
    k = sum(1 for r in resolved if r["dog_won"] == "1")
    p = k / n
    lo, hi = wilson(k, n)
    avg_res = sum(num(r, "entry_fill_price", 0) for r in resolved) / n
    print(f"  dog win rate: {k}/{n} = {c(f'{p*100:.1f}%', bold=True)}  95% CI [{lo*100:.1f}%, {hi*100:.1f}%]")
    print(f"  breakeven at your average entry of {avg_res:.3f} is {avg_res*100:.1f}%")
    edge = (p - avg_res) * 100
    if n < 50:
        print(c(f"  ⏳ n={n} is too small to call. (~{n_needed(0.05)} for +/-5pts.)", "yellow"))
    elif lo > avg_res:
        print(c(f"  ✅ base entry is +EV: {edge:+.1f}c/share, CI excludes breakeven.", "green", bold=True))
    elif hi < avg_res:
        print(c(f"  ✋ base entry is -EV: {edge:+.1f}c/share. The foundation is the problem,", "red", bold=True))
        print(c("     not the exit. No exit engine can fix a losing entry.", "red"))
    else:
        print(c(f"  🤷 inconclusive: {edge:+.1f}c/share but the CI straddles breakeven.", "yellow"))
    print(c("\n  NOTE: the 42.4% backtest figure covers ALL coa<=0.20 windows. You only", "cyan"))
    print(c("  buy where the ask was cheap, which selects the worse dogs. This number", "cyan"))
    print(c("  here — measured on YOUR population — is the one that counts.", "cyan"))


def main():
    live = "--live" in sys.argv
    tag = "" if live else "_paper"
    ev_path = os.path.join(DATA_DIR, f"flip_harvester_eval{tag}.csv")
    tr_path = os.path.join(DATA_DIR, f"flip_harvester_trades{tag}.csv")

    print(c("\n  💎 FLIP HARVESTER — RUN ANALYSIS  " + ("(LIVE)" if live else "(PAPER)"), "cyan", bold=True))
    print(f"  eval:   {ev_path}")
    print(f"  trades: {tr_path}")
    if not os.path.exists(ev_path) and not os.path.exists(tr_path):
        print(c("\n  Nothing to analyze — no CSVs at those paths. Run the bot first"
                " (or pass --live).", "yellow"))
        return

    ev, tr = rows(ev_path), rows(tr_path)
    q1_funnel(ev)
    q2_touch_and_book(tr)
    q3_adverse_selection(tr)
    q4_entry_edge(tr)

    section("WHAT THIS RUN HAS EARNED THE RIGHT TO CONCLUDE")
    print("  Kill it early if: the bid rarely reaches 0.62 (Q2), or the base entry")
    print("  measures -EV (Q4). Either one ends it, and both are cheap to learn.")
    print("  Only if Q2 and Q4 survive does q (Q3) matter — and that one needs")
    print(f"  ~{n_needed(0.04)} resolved sells, which is the expensive part.")
    print(c("\n  Do NOT read the P&L column as a verdict: ~1,200 trades before a real", "yellow"))
    print(c("  edge clears two standard errors. q and the fill rate are the signal.", "yellow"))
    print()


if __name__ == "__main__":
    main()
