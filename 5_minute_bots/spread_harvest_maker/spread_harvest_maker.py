"""
================================================================================
🌙 MOON DEV's SPREAD-HARVEST MAKER v1.0  (kimik3-july18 fleet #4)
================================================================================
The repo's first MID-PRICE MAKER on BTC 5-min markets. The June dog-log caught
17 windows in a single week where the dog ask was 0.60-0.68 in near-ties
(coa <= 0.43) — both asks >= 0.60, a >= 20c spread with the true state a coin
flip. Takers get slaughtered in those windows; a maker inside the spread gets
paid to wait. The 89c maker died (0 fills / 275 windows); cheap stink bids were
toxic (32-35% win); but MID-price maker fills at 0.40-0.50 were NOT toxic
(57% win @ 44c pooled, Coinbase-resolved). That's the shelf this bot quotes.

THE TRADE (BTC only, maker — never a taker):
  Gates (all must pass, evaluated every few seconds T-120 → T-30):
    1. coa = |cushion| / ATR4 <= 0.40        (verified coin flip)
    2. ask_sum = best_ask_up + best_ask_down >= 1.10   (book is WIDE — MMs absent)
    3. quote = min(dog_best_bid + 0.01, 0.48), floor 0.40, and strictly below
       the dog ask (never cross) → post_only GTC resting BUY on the dog side.
  Cancel the quote IMMEDIATELY on: coa > 0.60 (regime broke), ask_sum < 1.05
  (spread collapsed), or T-30 (time up). One quote per window.
  Filled → hold to resolution. Unfilled → cancelled, NO_FILL, move on.

ADVERSE SELECTION is the open question — this bot is built to answer it: every
quote logs coa_at_quote vs coa_at_fill vs mid_at_fill vs outcome. If the fills
are toxic, the CSV will say so within a week.

HONEST P&L (round-2 fix): resolve ONLY on crypto-price completed==true or Gamma
outcomePrices 1/0; PENDING never faked; W / L / PENDING separate; P&L resolved only.

LOGGING:
  - data/spread_harvest_eval.csv   — EVERY window: gate values + final action
    (SKIP_COA / SKIP_SPREAD / SKIP_PRICE_BAND / SKIP_CROSSED / QUOTED) — this is
    the ask_sum/spread DATASET the repo has never had
  - data/spread_harvest_trades.csv — quote lifecycle: quote_price, quote_result,
    coa_at_fill, mid_at_fill, fill_price, resolution persisted

🔴 PAPER_MODE = False — LIVE FIRE on the AUG14 account, $5 flat. Flip to True to paper.

📦 SINGLE-FILE: only pip dep is py_clob_client_v2. Repo-root .env required.
Account: AUG14 (signature_type 2, Gnosis Safe).
Built by Moon Dev 🌙
================================================================================
"""

import sys
import os
import time
import json
import csv
import math
import itertools
from collections import deque
import requests
from datetime import datetime, timezone, timedelta

# ============================================================================
# 🌙 MOON DEV - ZERO-DEP STAND-INS
# ============================================================================
ANSI = {"red": "31", "green": "32", "yellow": "33", "magenta": "35", "cyan": "36", "white": "37"}


def colored(text, color=None, attrs=None):
    codes = (["1"] if attrs and "bold" in attrs else []) + ([ANSI[color]] if color in ANSI else [])
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ============================================================================
# 🌙 MOON DEV - PATH / ENV / ACCOUNT (AUG14)
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BOT_DIR))
load_env(os.path.join(PROJECT_ROOT, '.env'))

ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 2  # AUG14 = Gnosis Safe

# ============================================================================
# 🌙 MOON DEV - KEY STRATEGY PARAMS (the microstructure gates)
# ============================================================================
COA_MAX = 0.40               # 🪙 verified coin flip only (|cushion|/ATR4)
COA_CANCEL = 0.60            # 🚨 regime broke — pull the quote immediately
ASK_SUM_MIN = 1.10           # 📖 only quote when the book is WIDE (MMs absent)
ASK_SUM_COLLAPSE = 1.05      # 🚨 spread collapsed under us — pull the quote
QUOTE_CAP = 0.48             # 💵 never bid above this (the mid-price shelf ceiling)
QUOTE_FLOOR = 0.40           # 💵 never bid below this (cheap fills were TOXIC: 32-35% win)
TICK = 0.01                  # Polymarket price tick
TIME_BAND = (30, 180)        # ⏱ quote live from T-120 → T-30 (seconds left)
USD_SIZE = 5                 # 💰 flat stake per fill
DAILY_STOP_LOSS = -30        # 🛑 halt new quotes at this realized day P&L
PAPER_MODE = False           # 🔴 LIVE on AUG14. True = log would-be quotes only

MIN_SHARES = 5
MARKET_DURATION = 300
CHECK_INTERVAL = 4           # seconds between gate evaluations
ATR_CACHE_SEC = 30           # refresh ATR4 this often
RESOLVE_GRACE_SEC = 15
ET = timezone(timedelta(hours=-4))

DATA_DIR = os.path.join(BOT_DIR, "data")
EVAL_LOG = os.path.join(DATA_DIR, "spread_harvest_eval.csv")
TRADES_FILE = os.path.join(DATA_DIR, "spread_harvest_trades.csv")
EVAL_FIELDS = ["timestamp", "market_ts", "slug", "cushion", "atr4", "coa",
               "dog_side", "dog_bid", "dog_ask", "fav_ask", "ask_sum",
               "minutes_left", "action", "paper"]
TRADE_FIELDS = ["timestamp", "market_ts", "slug", "side", "coa_at_quote", "ask_sum_at_quote",
                "quote_price", "quote_result", "coa_at_fill", "mid_at_fill",
                "fill_price", "fill_slippage", "shares", "usd",
                "result", "resolved_winner", "pnl_usd", "paper"]

# ============================================================================
# 🌙 MOON DEV - SESSION STATS
# ============================================================================
SESSION_START = time.time()
S = {"quotes": 0, "fills": 0, "skip_coa": 0, "skip_spread": 0, "skip_price": 0,
     "cancelled": 0, "daily_pnl": 0.0, "day": None, "halted": False}
SPINNER = itertools.cycle(["◐", "◓", "◑", "◒"])
EVENT_LOG = deque(maxlen=9)


def log_event(msg, color="white"):
    EVENT_LOG.append((datetime.now().strftime("%H:%M:%S"), msg, color))


# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (AUG14)
# ============================================================================
_CLIENT_CACHE = None


def _build_client():
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
    api_secret = os.getenv(f"API_SECRET{ACCOUNT_SUFFIX}") or os.getenv(f"SECRET{ACCOUNT_SUFFIX}")
    passphrase = os.getenv(f"PASSPHRASE{ACCOUNT_SUFFIX}")
    if api_key and api_secret and passphrase:
        client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase))
    else:
        client.set_api_creds(client.create_or_derive_api_creds())
    _CLIENT_CACHE = client
    return client


def place_maker_bid(token_id, price, size):
    """🌙 Moon Dev - resting post_only GTC BUY — we are the bid, never the taker."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="BUY")
    log_event(f"📖 QUOTE dog bid @ ${price:.3f} x{size} (post_only)", "cyan")
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=True)
        return resp or {}
    except Exception as e:
        err = str(e)
        if any(t in err.lower() for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
            log_event("⚠️ quote timed out — will verify via positions", "yellow")
            return {"status": "timeout"}
        log_event(f"❌ quote failed: {type(e).__name__}: {e}", "red")
        return {}


def cancel_token_orders(token_id):
    from py_clob_client_v2.clob_types import OrderMarketCancelParams
    try:
        _build_client().cancel_market_orders(OrderMarketCancelParams(asset_id=str(token_id)))
    except Exception as e:
        log_event(f"⚠️ cancel failed: {type(e).__name__}", "yellow")


def position_for_token(token_id):
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    r = safe_get("https://data-api.polymarket.com/positions",
                 {'user': pub, 'limit': 500, 'sortBy': 'CURRENT', 'sortDirection': 'DESC'})
    if r is None or r.status_code != 200:
        return (0.0, 0.0)
    for p in r.json():
        if str(p.get('asset')) == str(token_id):
            return (float(p.get('size', 0) or 0), float(p.get('avgPrice', 0) or 0))
    return (0.0, 0.0)


# ============================================================================
# 🌙 MOON DEV - MARKET DATA
# ============================================================================
def safe_get(url, params, timeout=10):
    try:
        return requests.get(url, params=params, timeout=timeout,
                            headers={'User-Agent': 'Mozilla/5.0 (moondev)'})
    except requests.exceptions.RequestException as e:
        log_event(f"🌐 net blip: {type(e).__name__}", "yellow")
        return None


def get_current_window_ts():
    return (int(time.time()) // MARKET_DURATION) * MARKET_DURATION


def get_market_tokens(window_ts):
    slug = f"btc-updown-5m-{window_ts}"
    r = safe_get("https://gamma-api.polymarket.com/markets", {'slug': slug, 'closed': 'false', 'active': 'true'})
    if r is None or r.status_code != 200:
        return None
    mk = r.json()
    if not mk:
        return None
    m = mk[0]
    try:
        outcomes = json.loads(m.get('outcomes', '[]'))
        tokens = json.loads(m.get('clobTokenIds', '[]'))
    except (ValueError, TypeError):
        return None
    if len(outcomes) != len(tokens) or not tokens:
        return None
    return {"slug": slug, "by_outcome": {o: t for o, t in zip(outcomes, tokens)}}


def get_strike(window_ts):
    """🌙 Moon Dev - the market's OWN price-to-beat (crypto-price openPrice)."""
    iso = datetime.fromtimestamp(window_ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    r = safe_get("https://polymarket.com/api/crypto/crypto-price",
                 {'symbol': 'btc', 'eventStartTime': iso})
    if r is None or r.status_code != 200:
        return None
    op = r.json().get('openPrice')
    return float(op) if op else None


def get_btc_mark():
    """🌙 Moon Dev - Hyperliquid BTC mark price."""
    try:
        r = requests.post("https://api.hyperliquid.xyz/info",
                          json={"type": "metaAndAssetCtxs"}, timeout=10)
    except requests.exceptions.RequestException as e:
        log_event(f"🌐 HL blip: {type(e).__name__}", "yellow")
        return None
    if r.status_code != 200:
        return None
    d = r.json()
    universe, ctxs = d[0]["universe"], d[1]
    for i, a in enumerate(universe):
        if a["name"] == "BTC":
            return float(ctxs[i]["markPx"])
    return None


_ATR_CACHE = {"ts": 0.0, "atr4": None}


def get_atr4():
    """🌙 Moon Dev - ATR4 = mean(high-low) of the last 4 COMPLETED 1-min BTC bars.
    Binance primary, Coinbase fallback. Cached ATR_CACHE_SEC seconds."""
    now = time.time()
    if _ATR_CACHE["atr4"] is not None and now - _ATR_CACHE["ts"] < ATR_CACHE_SEC:
        return _ATR_CACHE["atr4"]
    atr = None
    r = safe_get("https://api.binance.com/api/v3/klines", {'symbol': 'BTCUSDT', 'interval': '1m', 'limit': 5})
    if r is not None and r.status_code == 200:
        ks = r.json()
        if len(ks) >= 5:
            done = ks[:-1][-4:]  # drop the live bar, take the 4 completed
            atr = sum(float(k[2]) - float(k[3]) for k in done) / 4.0
    if atr is None:
        end = int(now) - (int(now) % 60)
        start = end - 5 * 60
        r = safe_get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                     {'granularity': 60,
                      'start': datetime.fromtimestamp(start, tz=timezone.utc).isoformat(),
                      'end': datetime.fromtimestamp(end, tz=timezone.utc).isoformat()})
        if r is not None and r.status_code == 200:
            cs = sorted(r.json(), key=lambda c: c[0])[-4:]  # [t, low, high, open, close, vol]
            if cs:
                atr = sum(float(c[2]) - float(c[1]) for c in cs) / len(cs)
    if atr and atr > 0:
        _ATR_CACHE["ts"] = now
        _ATR_CACHE["atr4"] = atr
        return atr
    return _ATR_CACHE["atr4"]  # stale cache beats None


def get_book(token_id):
    """🌙 Moon Dev - (best_bid, best_ask) for a token."""
    r = safe_get("https://clob.polymarket.com/book", {'token_id': token_id}, timeout=5)
    if r is None or r.status_code != 200:
        return (None, None)
    d = r.json()
    bids, asks = d.get('bids', []), d.get('asks', [])
    return (float(bids[-1]['price']) if bids else None,
            float(asks[-1]['price']) if asks else None)


def calc_shares(usd, price):
    shares = math.floor((usd / price) * 100) / 100
    return max(float(MIN_SHARES), shares)


def floor_tick(p):
    """🌙 Moon Dev - floor to the 0.01 tick — never rounds UP past the cap."""
    return math.floor(p * 100 + 1e-9) / 100.0


# ============================================================================
# 🌙 MOON DEV - CSV LOGGING
# ============================================================================
def append_csv(path, fields, row):
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


def read_csv_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv_rows(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def record_quote(window_ts, slug, side, coa, ask_sum, quote_price):
    """🌙 Moon Dev - a fresh resting bid enters the trades file as QUOTE_OPEN."""
    append_csv(TRADES_FILE, TRADE_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "market_ts": window_ts, "slug": slug, "side": side,
        "coa_at_quote": f"{coa:.4f}", "ask_sum_at_quote": f"{ask_sum:.4f}",
        "quote_price": f"{quote_price:.4f}", "quote_result": "OPEN",
        "coa_at_fill": "", "mid_at_fill": "", "fill_price": "", "fill_slippage": "",
        "shares": "", "usd": "",
        "result": "PENDING", "resolved_winner": "", "pnl_usd": "",
        "paper": int(PAPER_MODE)})


def update_open_quote(window_ts, **updates):
    """🌙 Moon Dev - stamp the lifecycle (FILLED / CANCELLED_*) onto the open quote row."""
    rows = read_csv_rows(TRADES_FILE)
    for r in reversed(rows):
        if r["market_ts"] == str(window_ts) and r["quote_result"] == "OPEN":
            for k, v in updates.items():
                r[k] = v
            break
    write_csv_rows(TRADES_FILE, TRADE_FIELDS, rows)


# ============================================================================
# 🌙 MOON DEV - HONEST RESOLUTION (definitive only)
# ============================================================================
def get_definitive_winner(market_ts, slug):
    iso = datetime.fromtimestamp(market_ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    r = safe_get("https://polymarket.com/api/crypto/crypto-price",
                 {'symbol': 'btc', 'eventStartTime': iso})
    if r is not None and r.status_code == 200:
        d = r.json()
        op, cp = d.get('openPrice'), d.get('closePrice')
        if d.get('completed') and op and cp:
            return "Up" if float(cp) >= float(op) else "Down"
    r = safe_get("https://gamma-api.polymarket.com/markets", {'slug': slug})
    if r is not None and r.status_code == 200 and r.json():
        try:
            prices = json.loads(r.json()[0].get('outcomePrices', '[]') or '[]')
        except (ValueError, TypeError):
            prices = []
        if len(prices) == 2:
            up = float(prices[0])
            if up == 1.0:
                return "Up"
            if up == 0.0:
                return "Down"
    return None


def resolve_pending_trades():
    """🌙 Moon Dev - resolve FILLED quotes only; OPEN quotes resolve via cancel/fill logic."""
    if not os.path.exists(TRADES_FILE):
        return
    rows = read_csv_rows(TRADES_FILE)
    changed = False
    for r in rows:
        if r["result"] != "PENDING" or r["quote_result"] != "FILLED":
            continue
        close_ts = int(r["market_ts"]) + MARKET_DURATION
        if time.time() < close_ts + RESOLVE_GRACE_SEC:
            continue
        winner = get_definitive_winner(int(r["market_ts"]), r["slug"])
        if winner is None:
            continue
        won = r["side"] == winner
        shares = float(r["shares"])
        cost = float(r["fill_price"]) * shares
        pnl = (shares - cost) if won else -cost
        r["result"] = "WIN" if won else "LOSS"
        r["resolved_winner"] = winner
        r["pnl_usd"] = f"{pnl:+.2f}"
        changed = True
        S["daily_pnl"] += pnl
        log_event(f"{'🏆 WON' if won else '💀 LOST'} {r['side']} vs {winner} ${pnl:+.2f} {r['slug']}",
                  "green" if won else "red")
    if changed:
        write_csv_rows(TRADES_FILE, TRADE_FIELDS, rows)


# ============================================================================
# 🌙 MOON DEV - WINDOW STATE
# ============================================================================
def fresh_state():
    return {"slug": None, "tokens": None, "strike": None, "quoted": False,
            "filled": False, "token_id": None, "quote_price": None, "quote_coa": None,
            "quote_ask_sum": None, "shares": None, "final_action": None}


def finalize_window(st, window_ts):
    """🌙 Moon Dev - one eval row per window: gate values + final action (the spread dataset)."""
    if st["slug"] is None:
        return
    append_csv(EVAL_LOG, EVAL_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "market_ts": window_ts, "slug": st["slug"],
        "cushion": st.get("cushion_s", ""), "atr4": st.get("atr4_s", ""), "coa": st.get("coa_s", ""),
        "dog_side": st.get("dog_s", ""), "dog_bid": st.get("dog_bid_s", ""), "dog_ask": st.get("dog_ask_s", ""),
        "fav_ask": st.get("fav_ask_s", ""), "ask_sum": st.get("ask_sum_s", ""),
        "minutes_left": st.get("ml_s", ""),
        "action": st["final_action"] or "NO_MARKET_DATA", "paper": int(PAPER_MODE)})


# ============================================================================
# 🌙 MOON DEV - THE HARVEST (one tick)
# ============================================================================
def harvest(st, window_ts, time_left, spot, atr4):
    """🌙 Moon Dev - quote the dog inside a wide coin-flip book; babysit the quote."""
    if st["tokens"] is None or st["strike"] is None or spot is None or atr4 is None:
        return

    cushion = spot - st["strike"]
    coa = abs(cushion) / atr4
    dog = "Down" if cushion > 0 else "Up"
    fav = "Up" if cushion > 0 else "Down"

    # ---- babysit an open quote: fill detection + cancel gates ----
    if st["quoted"] and not st["filled"]:
        held, avg = position_for_token(st["token_id"])
        if held > 0:
            # 🎣 FILLED — record the adverse-selection forensics, cancel remainder
            st["filled"] = True
            st["shares"] = held
            fill_px = avg if avg > 0 else st["quote_price"]
            dog_bid, dog_ask = get_book(st["token_id"])
            mid = None
            if dog_bid and dog_ask:
                mid = (dog_bid + dog_ask) / 2.0
            cancel_token_orders(st["token_id"])
            update_open_quote(window_ts,
                              quote_result="FILLED",
                              coa_at_fill=f"{coa:.4f}",
                              mid_at_fill="" if mid is None else f"{mid:.4f}",
                              fill_price=f"{fill_px:.4f}",
                              fill_slippage=f"{fill_px - st['quote_price']:+.4f}",
                              shares=f"{held:.2f}", usd=f"{fill_px * held:.2f}")
            S["fills"] += 1
            st["final_action"] = "FILLED"
            log_event(f"🎣 MAKER FILL {dog} @ ${fill_px:.3f} x{held:.0f} (coa now {coa:.2f}"
                      + (f", mid {mid:.3f}" if mid else "") + ")", "green")
            return
        # cancel gates: regime broke / spread collapsed / time up
        reason = None
        if coa > COA_CANCEL:
            reason = "CANCELLED_COA"
        elif time_left < TIME_BAND[0]:
            reason = "CANCELLED_TIME"
        else:
            dog_bid, dog_ask = get_book(st["token_id"])
            _, fav_ask = get_book(st["tokens"].get(fav))
            if dog_ask and fav_ask and (dog_ask + fav_ask) < ASK_SUM_COLLAPSE:
                reason = "CANCELLED_SPREAD"
        if reason:
            if not PAPER_MODE:
                cancel_token_orders(st["token_id"])
            update_open_quote(window_ts, quote_result=reason, result="NO_FILL")
            S["cancelled"] += 1
            st["final_action"] = reason
            log_event(f"✂️ quote pulled: {reason} (coa {coa:.2f}, {time_left:.0f}s left)", "yellow")
        return

    if st["quoted"] or st["filled"]:
        return  # one quote per window

    # ---- entry gates ----
    if not (TIME_BAND[0] <= time_left <= TIME_BAND[1]):
        st["final_action"] = st["final_action"] or "OUT_OF_TIME_BAND"
        return

    dog_bid, dog_ask = get_book(st["tokens"].get(dog))
    _, fav_ask = get_book(st["tokens"].get(fav))
    if dog_ask is None or fav_ask is None:
        st["final_action"] = "NO_BOOK"
        return
    ask_sum = dog_ask + fav_ask

    # snapshot for the eval row
    st["cushion_s"] = f"{cushion:+.2f}"
    st["atr4_s"] = f"{atr4:.2f}"
    st["coa_s"] = f"{coa:.4f}"
    st["dog_s"] = dog
    st["dog_bid_s"] = "" if dog_bid is None else f"{dog_bid:.4f}"
    st["dog_ask_s"] = f"{dog_ask:.4f}"
    st["fav_ask_s"] = f"{fav_ask:.4f}"
    st["ask_sum_s"] = f"{ask_sum:.4f}"
    st["ml_s"] = f"{time_left / 60:.2f}"

    if coa > COA_MAX:
        st["final_action"] = "SKIP_COA"
        S["skip_coa"] += 1
        return
    if ask_sum < ASK_SUM_MIN:
        st["final_action"] = "SKIP_SPREAD"
        S["skip_spread"] += 1
        return
    if dog_bid is None:
        st["final_action"] = "NO_BOOK"
        return

    quote = floor_tick(min(dog_bid + TICK, QUOTE_CAP))
    if quote < QUOTE_FLOOR:
        st["final_action"] = "SKIP_PRICE_BAND"
        S["skip_price"] += 1
        return
    if quote >= dog_ask:
        st["final_action"] = "SKIP_CROSSED"   # locked/crossed book — never cross as a maker
        return
    if S["halted"]:
        st["final_action"] = "HALTED"
        return

    # 🚀 ALL GATES PASSED — rest the dog bid inside the wide spread
    shares = calc_shares(USD_SIZE, quote)
    st["quoted"] = True
    st["token_id"] = st["tokens"].get(dog)
    st["quote_price"] = quote
    st["quote_coa"] = coa
    st["quote_ask_sum"] = ask_sum
    S["quotes"] += 1
    if PAPER_MODE:
        record_quote(window_ts, st["slug"], dog, coa, ask_sum, quote)
        st["final_action"] = "QUOTED"
        log_event(f"📝 PAPER QUOTE {dog} @ ${quote:.3f} x{shares:.0f} (coa {coa:.2f}, ask_sum {ask_sum:.2f})", "green")
        return
    resp = place_maker_bid(st["token_id"], quote, shares)
    if resp:
        record_quote(window_ts, st["slug"], dog, coa, ask_sum, quote)
        st["final_action"] = "QUOTED"
        log_event(f"📖 QUOTED {dog} @ ${quote:.3f} x{shares:.0f} (coa {coa:.2f}, ask_sum {ask_sum:.2f})", "cyan")
    else:
        st["final_action"] = "QUOTE_REJECTED"
        log_event(f"❌ quote rejected @ ${quote:.3f}", "red")


# ============================================================================
# 🌙 MOON DEV - DASHBOARD
# ============================================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def draw_tracker():
    rows = [r for r in read_csv_rows(TRADES_FILE) if r["quote_result"] == "FILLED"]
    print(colored(f"  ========== 🌙 MOON DEV'S FILL TRACKER ({len(rows)} fills) 🌙 ==========", "yellow", attrs=['bold']))
    for r in rows[-20:]:
        if r["result"] == "WIN":
            res, rcol = f"WIN  +${float(r['pnl_usd']):.2f}", "green"
        elif r["result"] == "LOSS":
            res, rcol = f"LOSS -${abs(float(r['pnl_usd'])):.2f}", "red"
        else:
            res, rcol = "PENDING", "yellow"
        print(colored(f"  {r['timestamp'][5:16]} | {r['side']:<4} bid {float(r['quote_price']):.2f} | ", "white")
              + colored(f"{res:<12}", rcol, attrs=['bold'])
              + colored(f"| https://polymarket.com/event/{r['slug']}", "cyan"))
    wins = sum(1 for r in rows if r["result"] == "WIN")
    losses = sum(1 for r in rows if r["result"] == "LOSS")
    pend = sum(1 for r in rows if r["result"] == "PENDING")
    pnl = sum(float(r["pnl_usd"]) for r in rows if r["result"] in ("WIN", "LOSS"))
    wr = wins / (wins + losses) * 100 if (wins + losses) else 0
    pcol = "green" if pnl >= 0 else "red"
    print(colored(f"  📊 W {wins} | L {losses} | PENDING {pend} | WR {wr:.1f}% | ", "white", attrs=['bold'])
          + colored(f"P&L (resolved only) ${pnl:+.2f}", pcol, attrs=['bold']))
    print(colored("  ========================================================================", "yellow", attrs=['bold']))


def draw(st, time_left, spot, atr4):
    clear_screen()
    draw_tracker()
    mode = colored("📝 PAPER", "yellow", attrs=['bold']) if PAPER_MODE else colored("🔴 LIVE · $5 real", "red", attrs=['bold'])
    print(colored("\n  📖 MOON DEV's SPREAD-HARVEST MAKER — paid to wait in wide books 📖  ", "cyan", attrs=['bold']) + mode + "\n")
    print(colored(f"  quotes {S['quotes']:<3} fills {S['fills']:<3} cancelled {S['cancelled']:<3} "
                  f"skips: coa {S['skip_coa']:<3} spread {S['skip_spread']:<3} price {S['skip_price']:<3}"
                  f"{' 🛑 DAILY STOP' if S['halted'] else ''}", "white"))
    mins, secs = max(0, time_left) // 60, max(0, time_left) % 60
    in_band = TIME_BAND[0] <= time_left <= TIME_BAND[1]
    print(colored(f"\n  ⏱  time left {mins}:{secs:02d}  ({'QUOTE BAND 🎯' if in_band else 'out of band'})",
                  "green" if in_band else "yellow", attrs=['bold']))
    if st["strike"] is None or spot is None or atr4 is None:
        print(colored(f"\n  BTC {next(SPINNER)} waiting for strike/spot/ATR...", "white"))
    else:
        cushion = spot - st["strike"]
        coa = abs(cushion) / atr4
        dog = "Down" if cushion > 0 else "Up"
        ccol = "green" if coa <= COA_MAX else ("red" if coa > COA_CANCEL else "white")
        status = ""
        if st["filled"]:
            status = colored(f"  🎣 FILLED x{st['shares']:.0f} — riding to resolution", "green", attrs=['bold'])
        elif st["quoted"]:
            status = colored(f"  📖 RESTING {dog} bid @ ${st['quote_price']:.3f} (coa@quote {st['quote_coa']:.2f})", "cyan", attrs=['bold'])
        elif st["final_action"]:
            status = colored(f"  [{st['final_action']}]", "yellow")
        print(colored(f"\n  BTC strike {st['strike']:>12,.2f}  spot {spot:>12,.2f}  cushion {cushion:+.2f}", "cyan"))
        print(colored(f"  ATR4 ${atr4:.2f}  coa {coa:.3f}", ccol, attrs=['bold'])
              + colored(f"  (gate ≤{COA_MAX}, cancel >{COA_CANCEL})  dog = {dog}", "white")
              + status)
        if st.get("ask_sum_s"):
            print(colored(f"  ask_sum {st['ask_sum_s']}  (gate ≥{ASK_SUM_MIN}, cancel <{ASK_SUM_COLLAPSE})  "
                          f"dog bid {st.get('dog_bid_s', '—')}  dog ask {st.get('dog_ask_s', '—')}", "white"))
    print()
    print(colored("  ┌───────────────────────── 📜 RECENT ─────────────────────────┐", "white"))
    for ts, msg, color in EVENT_LOG:
        print(colored((f"  │ {ts} {msg}")[:64].ljust(64) + "│", color))
    print(colored("  └──────────────────────────────────────────────────────────────┘", "white"))
    up = (time.time() - SESSION_START) / 60
    print(colored(f"\n  🌙 coa≤{COA_MAX} | ask_sum≥{ASK_SUM_MIN} | bid {QUOTE_FLOOR}-{QUOTE_CAP} | "
                  f"{TIME_BAND[0]}-{TIME_BAND[1]}s | ${USD_SIZE} | up {up:.1f}m — Ctrl+C", "magenta"))


# ============================================================================
# 🌙 MOON DEV - MAIN
# ============================================================================
def main():
    if not PAPER_MODE and (not os.getenv(PRIVATE_KEY_ENV_NAME) or not os.getenv(PUBLIC_KEY_ENV_NAME)):
        print(colored(f"❌ Missing {PRIVATE_KEY_ENV_NAME}/{PUBLIC_KEY_ENV_NAME} in .env", "red"))
        sys.exit(1)

    st = fresh_state()
    current_window = None
    last_draw = 0.0
    last_mark = 0.0
    last_check = 0.0
    spot = None
    log_event("📖 Spread-harvest maker spinning up" + (" (PAPER)" if PAPER_MODE else " — LIVE on AUG14"), "cyan")
    log_event(f"🎯 coa≤{COA_MAX} | ask_sum≥{ASK_SUM_MIN} | bid {QUOTE_FLOOR}-{QUOTE_CAP} | {TIME_BAND[0]}-{TIME_BAND[1]}s | ${USD_SIZE}", "cyan")

    while True:
        now = time.time()
        window_ts = get_current_window_ts()
        time_left = MARKET_DURATION - (int(now) - window_ts)

        today = datetime.now(ET).strftime("%Y-%m-%d")
        if S["day"] != today:
            S["day"] = today
            S["daily_pnl"] = 0.0
            S["halted"] = False
        if not S["halted"] and S["daily_pnl"] <= DAILY_STOP_LOSS:
            S["halted"] = True
            log_event(f"🛑 DAILY STOP {S['daily_pnl']:+.2f} <= {DAILY_STOP_LOSS} — no new quotes today", "red")

        resolve_pending_trades()

        if window_ts != current_window:
            if current_window is not None:
                # 🌙 safety: never carry a resting quote across the rollover
                if not PAPER_MODE and st["quoted"] and not st["filled"] and st["token_id"]:
                    cancel_token_orders(st["token_id"])
                    update_open_quote(current_window, quote_result="CANCELLED_TIME", result="NO_FILL")
                finalize_window(st, current_window)
                st = fresh_state()
            current_window = window_ts
            log_event(f"🔄 new window {datetime.fromtimestamp(window_ts, ET).strftime('%H:%M')} ET", "magenta")

        if st["tokens"] is None:
            info = get_market_tokens(window_ts)
            if info:
                st["tokens"] = info["by_outcome"]
                st["slug"] = info["slug"]
        if st["strike"] is None and st["tokens"] is not None:
            op = get_strike(window_ts)
            if op:
                st["strike"] = op
                log_event(f"🎯 strike {op:,.2f}", "cyan")

        if now - last_mark >= 1.0:
            last_mark = now
            px = get_btc_mark()
            if px:
                spot = px

        if now - last_check >= CHECK_INTERVAL:
            last_check = now
            harvest(st, window_ts, time_left, spot, get_atr4())

        if now - last_draw >= 0.5:
            last_draw = now
            draw(st, time_left, spot, _ATR_CACHE["atr4"])
        time.sleep(0.3)


if __name__ == "__main__":
    tag = "PAPER MODE — logging would-be quotes" if PAPER_MODE else "LIVE on AUG14 (real money, $5)"
    print(colored(f"🌙 Moon Dev's Spread-Harvest Maker — {tag}...", "cyan", attrs=['bold']))
    print(colored(f"📒 trades: {TRADES_FILE}\n📒 eval:   {EVAL_LOG}", "cyan"))
    time.sleep(0.4)
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print()
            print(colored("  ╔════════════════════════════════════════════════════╗", "yellow", attrs=['bold']))
            print(colored("  ║  📖 MOON DEV — SPREAD-HARVEST MAKER STOPPED 📖     ║", "yellow", attrs=['bold']))
            print(colored("  ╚════════════════════════════════════════════════════╝", "yellow", attrs=['bold']))
            print(colored(f"  🎯 quotes {S['quotes']}  fills {S['fills']}  day P&L ${S['daily_pnl']:+.2f}", "green", attrs=['bold']))
            print(colored(f"  💾 {TRADES_FILE}", "cyan"))
            print(colored("  🌙 Moon Dev out — paid to wait, never to chase.", "magenta"))
            print()
            break
        except Exception as e:
            print(colored(f"  💥 crashed: {type(e).__name__}: {e} — restarting in 3s", "red", attrs=['bold']))
            _CLIENT_CACHE = None
            time.sleep(3)
            continue
