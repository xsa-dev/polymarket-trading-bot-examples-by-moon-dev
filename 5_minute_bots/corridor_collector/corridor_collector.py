#!/opt/anaconda3/envs/tflow/bin/python
"""
================================================================================
🌙 MOON DEV's CORRIDOR COLLECTOR v1.0 (Fable July 18th Fleet, Idea #5)
================================================================================
TWO-SIDED market-structure bot on BTC 5-min + 15-min up/down markets.

THE STRUCTURE (idea_5_market_structure.md — 34,918 real 15-min windows):
  A 15-min window [T, T+900] contains the 5-min window [T+600, T+900] as its
  final third — BOTH resolve off the SAME close P15. Buy 15m-LEADER +
  5m-OPPOSITE and there is NO outcome where both legs lose:
      P15 beyond P10 (leader runs)      → $1  (15m leg pays)
      P0 < P15 < P10 (THE CORRIDOR)     → $2  (BOTH legs pay — the payday)
      P15 beyond P0 (full reversal)     → $1  (5m leg saves you)
  Fair pair value = 1 + P(corridor). In the sweet zone (lead 5-30 bps AND
  lead/ATR14 ≥ 1.0) the corridor hits 41.3% → fair sum ≈ $1.41. The floor
  held in 34,918 of 34,918 windows. The floor IS the stop.

ENTRY (first 90s of the final 5-min market, T+600 → T+690):
  ✅ Zone gate: lead 5-30 bps AND lead/ATR14 ≥ 1.0 (else log + skip)
  ✅ Price gate: ask15_leader + ask5_opposite ≤ 1 + p_corridor − 0.08 EDGE
  ✅ Sanity caps: ask5 ≤ 0.55, ask15 ≤ 0.93
  ✅ EQUAL shares both legs, marketable GTC back-to-back, one pair per window
  ✅ Hold BOTH legs to resolution — no stop, no TP, the $1 floor is the stop
  ❌ Never carry a naked leg: retry once (never at a worse price), then
     flatten at the bid and scream UNPAIRED

SIZING: Moon Dev wants $5 TOTAL per corridor pair — but Polymarket's 5-share
minimum per order means 5 shares/leg, so real cost lands ~$5-6 (we print the
actual total on every fire).

Account: AUG14 | CLOB V2 SDK (V1 post_order now throws PolyApiException)
Built by Moon Dev 🌙 — the floor is a feature, the corridor is the payday. 🚀
================================================================================
"""

import sys
import os

# 🌙 Moon Dev - Auto re-exec with tflow python if we're in the wrong env
TFLOW_PYTHON = "/opt/anaconda3/envs/tflow/bin/python"
if os.path.exists(TFLOW_PYTHON) and sys.executable != TFLOW_PYTHON:
    os.execv(TFLOW_PYTHON, [TFLOW_PYTHON] + sys.argv)

import time
import json
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from termcolor import colored

# ============================================================================
# 🌙 MOON DEV - PATH SETUP
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))                  # fable_july_18th/
PROJECT_ROOT = os.path.dirname(os.path.dirname(BOT_DIR))              # repo root
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

# ============================================================================
# 🌙 MOON DEV - KEY PARAMS (the whole edge lives in these numbers)
# ============================================================================
PAPER_MODE = False                  # 🌙 LIVE FIRE! Flip to True for paper testing

PAIR_BUDGET_USD = 5.0               # Moon Dev wants $5 TOTAL per corridor pair
MIN_SHARES = 5                      # Polymarket minimum order size (per leg!)
SHARES = MIN_SHARES                 # equal shares BOTH legs — corridor math is 1:1
                                    # 5sh x (ask15+ask5 ≈ 1.1-1.33) → real cost ~$5-6

ZONE_LEAD_BPS = (5.0, 30.0)         # sweet zone: lead 5-30 bps (41.3% corridor)
ZONE_MIN_LEAD_ATR = 1.0             # AND lead/ATR14 ≥ 1.0
EDGE = 0.08                         # fire at fair_sum − 0.08 (fair $1.41 → pay ≤ $1.33)
ASK5_CAP = 0.55                     # 5m-opposite is a coin flip — never pay > 55c
ASK15_CAP = 0.93                    # 15m leader sanity cap

# 🌙 Moon Dev - P(corridor) by 10-min lead size in bps (52 wks of REAL 1-min candles)
P_CORRIDOR_BINS = [                 # (lead_lo, lead_hi, p_corridor)
    (0.0,  2.0,  0.072),
    (2.0,  5.0,  0.219),
    (5.0,  10.0, 0.326),
    (10.0, 15.0, 0.405),
    (15.0, 20.0, 0.440),
    (20.0, 30.0, 0.464),
    (30.0, 50.0, 0.497),
]

WIN15 = 900                         # 15-min window seconds
ACTION_START = 600                  # act from T15+600 (the 5m open)...
ACTION_END = 690                    # ...through T15+690 (first 90s of final 5m)
CHECK_INTERVAL = 4                  # re-scan the pair every X seconds in-window
ATR_PERIOD = 14                     # ATR14 off 1-min bars ending at T15+600

KILL_SWITCH_CORRIDOR = 0.20         # pause if trailing corridor-hit rate < 20%
KILL_SWITCH_WINDOW = 30             # ...over last 30 resolved pairs
KILL_SWITCH_MIN_PAIRS = 15          # ...but only once 15 pairs have resolved

ET = timezone(timedelta(hours=-4))

# --- Files ---
DATA_DIR = os.path.join(BOT_DIR, "data")
LOG_FILE = os.path.join(DATA_DIR, "corridor_collector_log.csv")
LOG_FIELDS = ["time", "T15", "slug15", "slug5", "P0", "P10", "lead_bps", "atr14",
              "lead_atr", "p_corridor_table", "ask15", "ask5", "live_sum", "fair_sum",
              "gate_pass", "executed", "fill15", "fill5", "hedged", "P15",
              "corridor_hit", "payout", "cost", "pnl", "skip_reason"]

# ============================================================================
# 🌙 MOON DEV - ACCOUNT CONFIGURATION (AUG14, keeps OG free)
# ============================================================================
ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 2                  # Gnosis Safe

# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (taker orders — V1 post_order is dead)
# ============================================================================
_CLIENT_CACHE = None


def _build_client():
    """🌙 Moon Dev - cached CLOB V2 client for AUG14."""
    global _CLIENT_CACHE
    if _CLIENT_CACHE is not None:
        return _CLIENT_CACHE
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds
    from py_clob_client_v2.constants import POLYGON
    from web3 import Web3

    key = os.getenv(PRIVATE_KEY_ENV_NAME)
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    if not key or not pub:
        print(colored(f"❌ Moon Dev - Missing {PRIVATE_KEY_ENV_NAME}/{PUBLIC_KEY_ENV_NAME} in .env!", "red"))
        sys.exit(1)
    try:
        funder = Web3.toChecksumAddress(pub)
    except AttributeError:
        funder = Web3.to_checksum_address(pub)

    client = ClobClient(host="https://clob.polymarket.com", chain_id=POLYGON,
                        key=key, signature_type=SIGNATURE_TYPE, funder=funder)
    api_key = os.getenv(f"API_KEY{ACCOUNT_SUFFIX}")
    # 🌙 Moon Dev - AUG14's secret lives in .env as SECRET_AUG14 (NOT API_SECRET_AUG14)
    api_secret = os.getenv(f"API_SECRET{ACCOUNT_SUFFIX}") or os.getenv(f"SECRET{ACCOUNT_SUFFIX}")
    passphrase = os.getenv(f"PASSPHRASE{ACCOUNT_SUFFIX}")
    if api_key and api_secret and passphrase:
        client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase))
    else:
        client.set_api_creds(client.create_or_derive_api_creds())
    _CLIENT_CACHE = client
    return client


def place_taker(token_id, side, price, size):
    """🌙 Moon Dev - Marketable GTC that CROSSES the book (post_only=False).

    Why GTC and not FAK/FOK: Polymarket 400s marketable FAK/FOK orders when the
    crossable amount is < $1 — verified live. GTC crosses just the same and any
    unfilled remainder rests at our price (cancelled on window rollover)."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side=side.upper())
    print(colored(f"   🎯 Moon Dev - {side.upper()} {size:g} sh @ ${price:.2f} (GTC taker)", "cyan", attrs=['bold']))
    try:
        return client.create_and_post_order(args, order_type=OrderType.GTC, post_only=False) or {}
    except Exception as e:
        print(colored(f"   ❌ Moon Dev - {side} order failed: {type(e).__name__}: {e}", "red"))
        return {}


def cancel_token_orders(token_id):
    """🌙 Moon Dev - Cancel resting GTC remainders on a token (window rollover)."""
    from py_clob_client_v2.clob_types import OrderMarketCancelParams
    try:
        _build_client().cancel_market_orders(OrderMarketCancelParams(asset_id=str(token_id)))
    except Exception as e:
        print(colored(f"   ⚠️ Moon Dev - cancel failed: {type(e).__name__}", "yellow"))


def get_position_size(token_id):
    """🌙 Moon Dev - How many shares of token_id we actually hold (fill check)."""
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    r = requests.get("https://data-api.polymarket.com/positions",
                     params={'user': pub, 'limit': 500, 'sortBy': 'CURRENT', 'sortDirection': 'DESC'},
                     timeout=10)
    if r.status_code != 200:
        return 0.0
    for pos in r.json():
        if str(pos.get('asset')) == str(token_id):
            return float(pos.get('size', 0))
    return 0.0

# ============================================================================
# 🌙 MOON DEV - MARKET DISCOVERY (btc-updown-15m-{T15} + btc-updown-5m-{T15+600})
# ============================================================================

def get_market_tokens(slug):
    """🌙 Moon Dev - {'Up': token_id, 'Down': token_id} for a btc-updown slug."""
    r = requests.get("https://gamma-api.polymarket.com/markets",
                     params={'slug': slug, 'closed': 'false', 'active': 'true'}, timeout=10)
    if r.status_code != 200 or not r.json():
        return None
    m = r.json()[0]
    outs = json.loads(m.get('outcomes', '[]'))
    toks = json.loads(m.get('clobTokenIds', '[]'))
    if len(outs) != len(toks) or not toks:
        return None
    return {o: t for o, t in zip(outs, toks)}


def get_order_book(token_id):
    """🌙 Moon Dev - Best bid/ask from CLOB (bids ascending, asks descending)."""
    r = requests.get("https://clob.polymarket.com/book", params={'token_id': token_id}, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    bids, asks = data.get('bids', []), data.get('asks', [])
    if not bids or not asks:
        return None
    best_bid = float(bids[-1]['price'])
    best_ask = float(asks[-1]['price'])
    return {'best_bid': best_bid, 'best_ask': best_ask, 'spread': best_ask - best_bid}

# ============================================================================
# 🌙 MOON DEV - REAL BTC PRICE DATA (Binance 1-min, same source as 5min_15min_arb)
# ============================================================================

def get_klines(start_ts, count):
    """🌙 Moon Dev - `count` 1-min BTCUSDT klines starting at unix start_ts."""
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={'symbol': 'BTCUSDT', 'interval': '1m', 'limit': count,
                             'startTime': start_ts * 1000,
                             'endTime': (start_ts + count * 60 - 1) * 1000},
                     timeout=10)
    if r.status_code != 200 or not r.json():
        return None
    return r.json()


def bar_open(ts):
    """🌙 Moon Dev - open of the 1-min BTC bar that starts at unix ts. REAL data only."""
    k = get_klines(ts, 1)
    return float(k[0][1]) if k else None


def bar_close(ts):
    """🌙 Moon Dev - close of the 1-min BTC bar that starts at unix ts."""
    k = get_klines(ts, 1)
    return float(k[0][4]) if k else None


def atr14_at(ts):
    """🌙 Moon Dev - ATR14 (in $) off the 14 completed 1-min bars ending at unix ts."""
    k = get_klines(ts - (ATR_PERIOD + 1) * 60, ATR_PERIOD + 1)   # 15 bars → 14 TRs
    if not k or len(k) < ATR_PERIOD + 1:
        return None
    trs = []
    for i in range(1, len(k)):
        hi, lo, prev_close = float(k[i][2]), float(k[i][3]), float(k[i - 1][4])
        trs.append(max(hi - lo, abs(hi - prev_close), abs(lo - prev_close)))
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD


def p_corridor_lookup(lead_bps):
    """🌙 Moon Dev - hardcoded 52-week corridor table (lead bps bins)."""
    for lo, hi, p in P_CORRIDOR_BINS:
        if lo <= lead_bps < hi:
            return p
    return P_CORRIDOR_BINS[-1][2]   # ≥50 bps (outside zone anyway, logged only)

# ============================================================================
# 🌙 MOON DEV - LOGGING (one row EVERY 15-min window, entries AND skips)
# ============================================================================

def log_window(st, skip_reason=""):
    """🌙 Moon Dev - append the window row. The live_sum column on skips is the
    whole research payoff — live pair pricing vs the physical table."""
    os.makedirs(DATA_DIR, exist_ok=True)
    fmt = lambda v, d=2: "" if v is None else round(v, d)
    row = pd.DataFrame([{
        "time": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "T15": st['T15'], "slug15": st['slug15'], "slug5": st['slug5'],
        "P0": fmt(st['P0']), "P10": fmt(st['P10']),
        "lead_bps": fmt(st['lead_bps']), "atr14": fmt(st['atr14']),
        "lead_atr": fmt(st['lead_atr']), "p_corridor_table": fmt(st['p_corridor'], 3),
        "ask15": fmt(st['ask15']), "ask5": fmt(st['ask5']),
        "live_sum": fmt(st['best_sum'], 3), "fair_sum": fmt(st['fair_sum'], 3),
        "gate_pass": int(bool(st['gate_pass'])), "executed": int(bool(st['executed'])),
        "fill15": fmt(st['fill15']), "fill5": fmt(st['fill5']),
        "hedged": int(bool(st['hedged'])),
        "P15": "", "corridor_hit": "", "payout": "", "cost": "", "pnl": "",
        "skip_reason": skip_reason,
    }], columns=LOG_FIELDS)
    header = not os.path.exists(LOG_FILE)
    row.to_csv(LOG_FILE, mode='a', header=header, index=False)
    st['logged'] = True
    tag = "ENTER 🚀" if st['executed'] else f"SKIP ({skip_reason})"
    print(colored(f"   📝 Moon Dev - Logged window {st['T15']}: {tag}", "green" if st['executed'] else "yellow"))

# ============================================================================
# 🌙 MOON DEV - RESOLUTION (definitive gamma outcomePrices only) + KILL SWITCH
# ============================================================================

def _gamma_winner(slug):
    """🌙 Moon Dev - 'Up'/'Down' once gamma outcomePrices are definitive, else None."""
    r = requests.get("https://gamma-api.polymarket.com/markets", params={'slug': slug}, timeout=10)
    if r.status_code != 200 or not r.json():
        return None
    prices = json.loads(r.json()[0].get('outcomePrices', '[]') or '[]')
    if len(prices) != 2:
        return None
    up_price = float(prices[0])
    if up_price not in (0.0, 1.0):
        return None   # not resolved yet — no guessing, definitive data only
    return "Up" if up_price == 1.0 else "Down"


def resolve_pending_trades():
    """🌙 Moon Dev - fill in P15/corridor_hit/payout/pnl on executed hedged pairs
    once BOTH markets resolve (gamma outcomePrices — the honest-P&L standard)."""
    if not os.path.exists(LOG_FILE):
        return
    df = pd.read_csv(LOG_FILE)
    pending = df[(df['executed'] == 1) & (df['hedged'] == 1) & (df['pnl'].isna())]
    if pending.empty:
        return
    changed = False
    for idx, row in pending.iterrows():
        if time.time() < row['T15'] + WIN15 + 30:
            continue   # give the oracle a beat
        win15 = _gamma_winner(row['slug15'])
        win5 = _gamma_winner(row['slug5'])
        if win15 is None or win5 is None:
            continue
        # 🌙 leader side = 15m leg we bought; 5m leg is the opposite side
        s15 = "Up" if float(row['P10']) >= float(row['P0']) else "Down"
        s5 = "Down" if s15 == "Up" else "Up"
        won15, won5 = int(win15 == s15), int(win5 == s5)
        corridor = int(won15 and won5)
        payout = SHARES * (won15 + won5)
        cost = SHARES * (float(row['fill15']) + float(row['fill5']))
        pnl = payout - cost
        P15 = bar_close(row['T15'] + WIN15 - 60)   # shared close, for the log
        df.at[idx, 'P15'] = "" if P15 is None else round(P15, 2)
        df.at[idx, 'corridor_hit'] = corridor
        df.at[idx, 'payout'] = round(payout, 2)
        df.at[idx, 'cost'] = round(cost, 2)
        df.at[idx, 'pnl'] = round(pnl, 2)
        changed = True
        if corridor:
            print(colored(f"   💰 Moon Dev DOUBLE WIN — close landed in the corridor! +${pnl:.2f}", "green", attrs=['bold']))
        elif won5:
            print(colored(f"   🧱 Moon Dev floor save — full reversal, 5m leg paid. -${-pnl:.2f} only.", "yellow"))
        else:
            print(colored(f"   🏁 Moon Dev - leader ran, 15m leg paid the floor. ${pnl:+.2f}", "yellow"))
    if changed:
        df.to_csv(LOG_FILE, index=False)


def kill_switch_active():
    """🌙 Moon Dev - True = PAUSE. Trailing-30 realized corridor rate < 20%
    (table says 41.3% in-zone — half that means the zone is lying, stand down)."""
    if not os.path.exists(LOG_FILE):
        return False
    df = pd.read_csv(LOG_FILE)
    resolved = df[(df['executed'] == 1) & (df['pnl'].notna())].tail(KILL_SWITCH_WINDOW)
    if len(resolved) < KILL_SWITCH_MIN_PAIRS:
        return False
    rate = resolved['corridor_hit'].mean()
    if rate < KILL_SWITCH_CORRIDOR:
        print(colored(f"   🚨 Moon Dev - KILL SWITCH! Trailing-{len(resolved)} corridor rate "
                      f"{rate*100:.1f}% < {KILL_SWITCH_CORRIDOR*100:.0f}% — entries PAUSED", "red", attrs=['bold']))
        return True
    return False


def print_trade_tracker():
    """🌙 Moon Dev - Clickable tracker of past pairs, printed FIRST every window."""
    if not os.path.exists(LOG_FILE):
        return
    df = pd.read_csv(LOG_FILE)
    trades = df[df['executed'] == 1]
    if trades.empty:
        return
    print(colored(f"========== 🌙 MOON DEV'S TRADE TRACKER ({len(trades)} pairs) 🌙 ==========", "cyan", attrs=['bold']))
    for _, t in trades.tail(15).iterrows():
        if pd.notna(t['pnl']):
            if int(t['corridor_hit']) == 1:
                status, color = f"DOUBLE 💰 +${float(t['pnl']):.2f}", "green"
            else:
                status, color = f"FLOOR ${float(t['pnl']):+.2f}", ("green" if float(t['pnl']) >= 0 else "red")
        elif int(t['hedged']) != 1:
            status, color = "UNPAIRED ⚠️", "red"
        else:
            status, color = "PENDING", "white"
        cost = (float(t['fill15']) + float(t['fill5'])) * SHARES if pd.notna(t['fill15']) and pd.notna(t['fill5']) else 0
        print(colored(f"{t['time'][5:16]} | sum {float(t['live_sum']):.3f} x{SHARES:g}sh (${cost:.2f}) | {status:<18} | "
                      f"https://polymarket.com/event/{t['slug15']}", color))
    print(colored("=" * 70, "cyan", attrs=['bold']))


def print_session_summary():
    """🌙 Moon Dev - Quick corridor-rate + PnL readout."""
    if not os.path.exists(LOG_FILE):
        print(colored("   📋 Moon Dev - No window history yet (first run)", "yellow"))
        return
    df = pd.read_csv(LOG_FILE)
    trades = df[df['executed'] == 1]
    resolved = trades[trades['pnl'].notna()]
    corr = int(resolved['corridor_hit'].sum()) if len(resolved) else 0
    print(colored(f"\n📋 Moon Dev - CORRIDOR COLLECTOR HISTORY", "cyan", attrs=['bold']))
    print(colored(f"   Windows: {len(df)} | Pairs: {len(trades)} | Resolved: {len(resolved)} | "
                  f"Corridor hits: {corr} ({(corr/len(resolved)*100) if len(resolved) else 0:.1f}%) | "
                  f"PnL: ${resolved['pnl'].sum() if len(resolved) else 0:+.2f}", "white"))

# ============================================================================
# 🌙 MOON DEV - PAIR EXECUTION (both legs back-to-back, orphan handling)
# ============================================================================

def execute_pair(st):
    """🌙 Moon Dev - take BOTH legs at their asks, equal shares. Leg 2 failure
    after leg 1 fill = UNPAIRED, logged LOUDLY. Retry only at same-or-better
    price (never chase worse), then flatten the orphan at the bid."""
    t15, t5 = st['tok15'][st['s15']], st['tok5'][st['s5']]
    ask15, ask5 = st['ask15'], st['ask5']
    est_cost = SHARES * (ask15 + ask5)
    print(colored(f"\n   🔥🔥🔥 MOON DEV CORRIDOR COLLECT: buy 15m-{st['s15']} @ ${ask15:.2f} + "
                  f"5m-{st['s5']} @ ${ask5:.2f} | sum ${ask15+ask5:.3f} vs fair ${st['fair_sum']:.3f} "
                  f"| {SHARES:g} sh/leg ≈ ${est_cost:.2f} total 🔥🔥🔥", "green", attrs=['bold']))
    print(colored(f"   🔗 https://polymarket.com/event/{st['slug15']}", "cyan"))
    print(colored(f"   🔗 https://polymarket.com/event/{st['slug5']}", "cyan"))

    if PAPER_MODE:
        print(colored(f"   📄 Moon Dev - PAPER MODE: simulated pair fill, total ${est_cost:.2f}", "yellow", attrs=['bold']))
        st['fill15'], st['fill5'], st['hedged'] = ask15, ask5, True
        return

    place_taker(t15, "BUY", ask15, SHARES)
    place_taker(t5, "BUY", ask5, SHARES)
    time.sleep(2)
    f15, f5 = get_position_size(t15), get_position_size(t5)

    # 🌙 Moon Dev - one retry on an orphaned leg, NEVER at a worse price
    if f15 > 0 and f5 <= 0:
        book = get_order_book(t5)
        if book and book['best_ask'] <= ask5:
            print(colored(f"   ↻ Moon Dev - retrying 5m-{st['s5']} leg @ ${book['best_ask']:.2f}", "yellow"))
            place_taker(t5, "BUY", book['best_ask'], SHARES)
            time.sleep(2)
            f5 = get_position_size(t5)
    elif f5 > 0 and f15 <= 0:
        book = get_order_book(t15)
        if book and book['best_ask'] <= ask15:
            print(colored(f"   ↻ Moon Dev - retrying 15m-{st['s15']} leg @ ${book['best_ask']:.2f}", "yellow"))
            place_taker(t15, "BUY", book['best_ask'], SHARES)
            time.sleep(2)
            f15 = get_position_size(t15)

    st['hedged'] = (f15 > 0 and f5 > 0)
    st['fill15'] = ask15 if f15 > 0 else None
    st['fill5'] = ask5 if f5 > 0 else None

    if st['hedged']:
        actual = SHARES * (ask15 + ask5)
        print(colored(f"   ✅ Moon Dev - BOTH LEGS FILLED — pair locked, actual total ${actual:.2f}. "
                      f"Holding to resolution, the floor is the stop! 🧱", "green", attrs=['bold']))
    else:
        # 🌙 Moon Dev - UNPAIRED! Scream it, flatten the orphan, no chasing.
        print(colored(f"\n   🚨🚨🚨 MOON DEV UNPAIRED LEG! fill15={f15:g} fill5={f5:g} — "
                      f"one leg naked, flattening at the bid NOW 🚨🚨🚨", "red", attrs=['bold']))
        if f15 > 0 and f5 <= 0:
            cancel_token_orders(t5)
            book = get_order_book(t15)
            if book:
                place_taker(t15, "SELL", book['best_bid'], f15)
        elif f5 > 0 and f15 <= 0:
            cancel_token_orders(t15)
            book = get_order_book(t5)
            if book:
                place_taker(t5, "SELL", book['best_bid'], f5)

# ============================================================================
# 🌙 MOON DEV - WINDOW STATE + EVALUATION
# ============================================================================

def new_state(T15):
    return {"T15": T15, "slug15": f"btc-updown-15m-{T15}", "slug5": f"btc-updown-5m-{T15 + ACTION_START}",
            "tok15": None, "tok5": None, "P0": None, "P10": None, "atr14": None,
            "lead_bps": None, "lead_atr": None, "p_corridor": None, "fair_sum": None,
            "s15": None, "s5": None, "ask15": None, "ask5": None, "best_sum": None,
            "gate_pass": False, "executed": False, "hedged": False,
            "fill15": None, "fill5": None, "logged": False, "skip_reason": "NO_DATA",
            "last_check": 0.0, "paused": False}


def evaluate_window(st):
    """🌙 Moon Dev - one gate gauntlet pass inside T15+600 → T15+690.
    Returns True when the window is DONE (fired or hard-skipped)."""
    T15 = st['T15']

    # --- markets ---
    if st['tok15'] is None:
        st['tok15'] = get_market_tokens(st['slug15'])
    if st['tok5'] is None:
        st['tok5'] = get_market_tokens(st['slug5'])
    if not st['tok15'] or not st['tok5']:
        st['skip_reason'] = "NO_MARKET"
        print(colored("   🔄 Moon Dev - markets not indexed yet, retrying...", "yellow"))
        return False

    # --- real BTC strikes + vol (Binance 1m, NEVER faked) ---
    if st['P0'] is None:
        st['P0'] = bar_open(T15)
    if st['P10'] is None:
        st['P10'] = bar_open(T15 + ACTION_START)
    if st['atr14'] is None:
        st['atr14'] = atr14_at(T15 + ACTION_START)
    if st['P0'] is None or st['P10'] is None or st['atr14'] is None or st['atr14'] <= 0:
        st['skip_reason'] = "NO_PRICE_DATA"
        print(colored("   🔄 Moon Dev - waiting on real BTC price data...", "yellow"))
        return False

    # --- leader + zone stats ---
    lead_abs = abs(st['P10'] - st['P0'])
    st['lead_bps'] = lead_abs / st['P0'] * 10_000
    st['lead_atr'] = lead_abs / st['atr14']
    st['s15'] = "Up" if st['P10'] >= st['P0'] else "Down"
    st['s5'] = "Down" if st['s15'] == "Up" else "Up"
    st['p_corridor'] = p_corridor_lookup(st['lead_bps'])
    st['fair_sum'] = 1.0 + st['p_corridor']

    # --- live asks (read BEFORE zone gate so skips still log the pair sum) ---
    b15 = get_order_book(st['tok15'][st['s15']])
    b5 = get_order_book(st['tok5'][st['s5']])
    st['ask15'] = b15['best_ask'] if b15 else None
    st['ask5'] = b5['best_ask'] if b5 else None
    if st['ask15'] is not None and st['ask5'] is not None:
        ssum = st['ask15'] + st['ask5']
        st['best_sum'] = ssum if st['best_sum'] is None else min(st['best_sum'], ssum)
    else:
        ssum = None

    zone = (ZONE_LEAD_BPS[0] <= st['lead_bps'] <= ZONE_LEAD_BPS[1]
            and st['lead_atr'] >= ZONE_MIN_LEAD_ATR)
    print(colored(f"   🧭 Moon Dev: 15m leads {st['s15']} {st['lead_bps']:.1f}bps "
                  f"({st['lead_atr']:.1f}x ATR) → corridor {st['p_corridor']*100:.0f}% → "
                  f"fair ${st['fair_sum']:.2f} | live sum "
                  f"{'$%.3f' % ssum if ssum is not None else 'n/a'} "
                  f"{'🎯 IN ZONE' if zone else '😴 out of zone'}", "cyan"))

    # --- GATE 1: sweet zone (lead 5-30 bps AND lead/ATR ≥ 1.0) ---
    if not zone:
        st['skip_reason'] = "ZONE_LEAD" if st['lead_atr'] >= ZONE_MIN_LEAD_ATR else "ZONE_ATR"
        return True   # zone won't change this window — log and move on

    # --- GATE 2: books ---
    if ssum is None:
        st['skip_reason'] = "NO_BOOK"
        return False

    # --- GATE 3: sanity caps ---
    if st['ask5'] > ASK5_CAP:
        st['skip_reason'] = "ASK5_CAP"
        return False
    if st['ask15'] > ASK15_CAP:
        st['skip_reason'] = "ASK15_CAP"
        return False

    # --- GATE 4: the price gate — pay fair minus EDGE or nothing ---
    if ssum > st['fair_sum'] - EDGE:
        st['skip_reason'] = "SUM_TOO_RICH"
        return False

    if st['paused']:
        st['skip_reason'] = "KILL_SWITCH"
        return False

    # --- ALL GATES PASSED → COLLECT! 🚀 ---
    st['gate_pass'] = True
    print(colored(f"   🎯 Moon Dev: lead {st['lead_bps']:.1f}bps ({st['lead_atr']:.1f}x ATR) → "
                  f"corridor {st['p_corridor']*100:.0f}% → fair ${st['fair_sum']:.2f} | "
                  f"live sum ${ssum:.3f} → COLLECT!", "green", attrs=['bold']))
    st['executed'] = True
    st['skip_reason'] = ""
    execute_pair(st)
    log_window(st, "" if st['hedged'] else "UNPAIRED")
    return True

# ============================================================================
# 🌙 MOON DEV - MAIN
# ============================================================================

def main():
    print(colored("""
╔══════════════════════════════════════════════════════════════════════════════╗
║   🌙 MOON DEV's CORRIDOR COLLECTOR v1.0 🌙                                  ║
║   15m-leader + 5m-opposite pair — the $1 floor is the stop,                 ║
║   the $2 corridor (41.3% in-zone) is the payday                             ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """, "cyan", attrs=['bold']))

    print(colored("⚙️  Moon Dev - Configuration:", "yellow", attrs=['bold']))
    print(colored(f"   📄 PAPER MODE:     {PAPER_MODE} {'(flip to False for live!)' if PAPER_MODE else '🔴 LIVE FIRE'}", "yellow" if PAPER_MODE else "red"))
    print(colored(f"   💵 Size:           ${PAIR_BUDGET_USD:.0f} target/pair → {SHARES:g} sh/leg (5-share min) → real cost ~$5-6, printed on fire", "white"))
    print(colored(f"   🎯 Zone gate:      lead {ZONE_LEAD_BPS[0]:.0f}-{ZONE_LEAD_BPS[1]:.0f} bps AND lead/ATR14 ≥ {ZONE_MIN_LEAD_ATR:.1f}", "white"))
    print(colored(f"   💰 Price gate:     ask15+ask5 ≤ 1 + p_corridor − {EDGE:.2f} | caps ask5 ≤ {ASK5_CAP:.2f}, ask15 ≤ {ASK15_CAP:.2f}", "white"))
    print(colored(f"   ⏳ Action window:  T15+{ACTION_START}s → T15+{ACTION_END}s (first 90s of the final 5m)", "white"))
    print(colored(f"   🧱 Exit:           hold BOTH legs to resolution — the floor IS the stop", "white"))
    print(colored(f"   🚨 Kill switch:    trailing-{KILL_SWITCH_WINDOW} corridor rate < {KILL_SWITCH_CORRIDOR*100:.0f}% → pause", "white"))

    print_session_summary()
    print("")
    print_trade_tracker()

    # 🌙 Moon Dev - smoke-test the REAL price feed before going live (no data = no bot)
    print(colored("\n📡 Moon Dev - Testing Binance 1m feed...", "yellow", attrs=['bold']))
    now_min = (int(time.time()) // 60) * 60
    px = bar_open(now_min - 60)
    atr = atr14_at(now_min)
    if px is None or atr is None:
        print(colored("❌ Moon Dev - BTC price feed is dead — refusing to run on no data!", "red"))
        sys.exit(1)
    print(colored(f"   BTC 1m open: ${px:,.2f} | ATR14: ${atr:,.2f}", "cyan"))

    print(colored(f"\n{'='*70}", "green"))
    print(colored("🚀 Moon Dev's CORRIDOR COLLECTOR — hunting the $2 corridor...", "green", attrs=['bold']))
    print(colored("   Press Ctrl+C to stop", "yellow"))
    print(colored(f"{'='*70}\n", "green"))

    st = new_state((int(time.time()) // WIN15) * WIN15)
    last_wait_print = 0.0

    try:
        while True:
            now = time.time()
            T15 = (int(now) // WIN15) * WIN15
            elapsed = int(now) - T15

            # --- rollover: close out the old 15-min window ---
            if T15 != st['T15']:
                if st['executed'] and not PAPER_MODE:
                    # 🌙 Moon Dev - cancel any resting GTC remainders on rollover
                    cancel_token_orders(st['tok15'][st['s15']])
                    cancel_token_orders(st['tok5'][st['s5']])
                if not st['logged']:
                    log_window(st, st['skip_reason'])
                st = new_state(T15)
                print("")
                print_trade_tracker()
                print(colored(f"\n{'='*70}", "cyan"))
                print(colored(f"🌙 Moon Dev - NEW 15-MIN WINDOW | T15={T15} | "
                              f"{datetime.fromtimestamp(T15, tz=ET).strftime('%I:%M:%S%p ET')}", "cyan", attrs=['bold']))
                print(colored(f"   🔗 https://polymarket.com/event/{st['slug15']}", "cyan"))
                print(colored(f"   🔗 https://polymarket.com/event/{st['slug5']}", "cyan"))
                print(colored(f"{'='*70}", "cyan"))
                resolve_pending_trades()
                st['paused'] = kill_switch_active()

            # --- the action window: T15+600 → T15+690 ---
            if ACTION_START <= elapsed <= ACTION_END and not st['logged']:
                if now - st['last_check'] >= CHECK_INTERVAL:
                    st['last_check'] = now
                    print(colored(f"\n   📡 Moon Dev - scanning the corridor... ({ACTION_END - elapsed}s of action window left)", "yellow"))
                    if evaluate_window(st):
                        if not st['logged']:
                            log_window(st, st['skip_reason'])
            elif elapsed > ACTION_END and not st['logged']:
                # window over, never fired — log the skip with the last known stats
                log_window(st, st['skip_reason'])
            elif elapsed < ACTION_START and now - last_wait_print >= 60:
                last_wait_print = now
                print(colored(f"   ⏳ Moon Dev - waiting for the final 5m... action window opens in {ACTION_START - elapsed}s", "white"))

            if st['executed'] and st['hedged'] and int(now) % 30 == 0:
                print(colored(f"   🧱 Moon Dev - holding the pair to resolution... {WIN15 - elapsed}s to close", "white"))
                time.sleep(1)

            time.sleep(1)

    except KeyboardInterrupt:
        print(colored("\n\n🛑 Moon Dev - Corridor Collector stopped! Clean shutdown ✅", "yellow", attrs=['bold']))
        if st['executed'] and not PAPER_MODE and st['tok15'] and st['tok5']:
            cancel_token_orders(st['tok15'][st['s15']])
            cancel_token_orders(st['tok5'][st['s5']])
        if not st['logged'] and st['lead_bps'] is not None:
            log_window(st, st['skip_reason'] or "SHUTDOWN")
        print_session_summary()
        print(colored(f"   💾 {LOG_FILE}", "cyan"))
        print(colored("   🌙 The floor is a feature, the corridor is the payday. LFG! 🚀", "magenta"))


if __name__ == "__main__":
    print("🌙 Moon Dev's Corridor Collector - collecting the $2 corridor, let's go! 🚀")
    main()
