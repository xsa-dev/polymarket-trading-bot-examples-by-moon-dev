"""
================================================================================
🌙 MOON DEV's FLIP HARVESTER v1.0  (the coin-flip dog's exit engine)
================================================================================
Every dog bot in this fleet buys the coin-flip underdog cheap and HOLDS to
resolution. The 52-week physical backtest (BTCUSD-1m-52wks-data.csv,
104,669 windows) says that's leaving money on the table: a dog with
coa = |cushion|/ATR4 <= 0.20 TOUCHES the strike (briefly leads) 71.3% of the
time (n=10,180) but only WINS 42.4%. A no-touch dog can never win, so that
29-point gap is pure "touch-then-fade" — real value nobody in the fleet
harvests, because nobody's exit code has ever run before resolution.

THE TRADE:
  ENTRY  (identical gate to the fleet's proven coinflip-dog pocket):
    coa = |cushion| / ATR4 <= 0.20  AND  |cushion| <= 1.5 bps of spot
    dog ask 0.22-0.45 (sub-0.22 is correctly-priced trash, above 0.45 the
    market already priced the move — both measured, both -EV in the logs)
  EXIT (the new part):
    The instant the buy fills, rest a post-only GTC SELL at 0.62 (or 0.65,
    A/B'd by window parity). HIGH-vol windows harvest 100% of shares
    (touches fade back 55.3% of the time there), LOW-vol windows harvest
    50% and hold the rest (touches STICK 60.9% of the time there, the hold
    leg is the better horse). Breakeven flip price is 0.5947 — 0.62 has
    room. Unsold shares just ride to resolution like the rest of the fleet:
    worst case we ARE the incumbent dog bot, this is a free option on top.

WHY 0.62 AND NOT LOWER: below ~0.595 the harvest EV is worse than holding.
No stop-loss, no cancel-and-chase downward, ever — that discipline is the
whole point (see flip_harvester_IDEA.md for the full EV table).

RISK: the 62c print is the ONE unproven link (does the CLOB ever actually
BID that high during a flip?). `touched` and `max_bid_after_touch` are
logged on every entered window specifically to answer that question before
this bot is ever trusted with more than $10.

🟡 PAPER_MODE = True — this idea has never traded live. Flip to False only
after the eval CSV shows real sell fills at a realistic rate. See the repo
README: "nothing plug-and-play," this includes bots Moon Dev hasn't proven.

📦 SINGLE-FILE: only pip dep is py_clob_client_v2 (+ requests, stdlib).
Repo-root .env required. Account: AUG14 (signature_type 2, Gnosis Safe).
Built by Moon Dev 🌙, from flip_harvester_IDEA.md.
================================================================================
"""

import sys
import os
import time
import json
import csv
import math
import statistics
import itertools
from collections import deque
import requests
from datetime import datetime, timezone, timedelta

# ============================================================================
# 🌙 MOON DEV - ZERO-DEP STAND-INS (stdlib only, py_clob_client_v2 is the only pip)
# ============================================================================
ANSI = {"red": "31", "green": "32", "yellow": "33", "magenta": "35", "cyan": "36", "white": "37"}


def colored(text, color=None, attrs=None):
    """🌙 Moon Dev - inline termcolor replacement (ANSI escapes, stdlib only)."""
    codes = (["1"] if attrs and "bold" in attrs else []) + ([ANSI[color]] if color in ANSI else [])
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else str(text)


def load_env(path):
    """🌙 Moon Dev - inline dotenv replacement: parse KEY=VALUE lines into os.environ."""
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
# 🌙 MOON DEV - PATH / ENV / ACCOUNT (AUG14 — multi-account structure)
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BOT_DIR))
load_env(os.path.join(PROJECT_ROOT, '.env'))

ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 1 if ACCOUNT_SUFFIX == "" else 2  # OG=1, sub-accounts=2 (Gnosis Safe)

# ============================================================================
# 🌙 MOON DEV - KEY STRATEGY PARAMS (the mined cells — the whole edge lives here)
# ============================================================================
COA_MAX = 0.20                # 🎯 |cushion| / ATR4 gate — the physically-validated touch pocket
CUSHION_BPS_MAX = 1.5         # 🎯 |cushion| must also be <= this many bps of spot
DOG_ASK_BAND = (0.22, 0.45)   # 💵 sub-0.22 is priced trash, above 0.45 the move is already in
ENTRY_TIME_BAND = (5, 60)     # ⏱ seconds remaining: T-60 -> T-5 (bars 0-3 are closed by T-60)

EXIT_ASK_A = 0.62             # 🪑 primary flip-harvest ask (breakeven is 0.5947, this is the margin)
EXIT_ASK_B = 0.65             # 🪑 A/B alt ask — 52wk math says +2.1c/share IF touches overshoot it
AB_TEST_ENABLED = True        # alternate A/B by window parity for the first live batch

HIGH_VOL_SELL_PCT = 1.00      # HIGH vol: touches FADE (55.3% stick) -> harvest everything
LOW_VOL_SELL_PCT = 0.50       # LOW vol: touches STICK (60.9%) -> harvest half, hold half
ATR_MIN_SAMPLES = 20          # need this many windows of ATR4 history before trusting the median
ATR_HISTORY_SEC = 24 * 3600   # rolling 24h median, per the idea doc

USD_SIZE = 10                 # 💰 flat stake per entry ($10, fleet standard for a fresh idea)
DAILY_STOP_LOSS = -60         # 🛑 halt new entries if realized day P&L hits this (~6 full losses)
MAX_CONCURRENT_PENDING = 3    # 🛑 don't stack more than 3 unresolved windows at once
PAPER_MODE = True             # 🟡 UNPROVEN — logs would-be fills only. Flip after real sell data.

MIN_SHARES = 5                # Polymarket 5-share minimum per order
PRICE_TICK = 0.01
MARKET_DURATION = 300          # 5-minute windows
CHECK_INTERVAL = 3             # re-check a hot book every X sec
RESOLVE_GRACE_SEC = 15         # first resolution poll this many sec after close
ET = timezone(timedelta(hours=-4))

DATA_DIR = os.path.join(BOT_DIR, "data")
EVAL_LOG = os.path.join(DATA_DIR, "flip_harvester_eval.csv")
TRADES_FILE = os.path.join(DATA_DIR, "flip_harvester_trades.csv")
ATR_HISTORY_FILE = os.path.join(DATA_DIR, "flip_harvester_atr_history.json")

EVAL_FIELDS = ["timestamp", "window_ts", "slug", "strike", "spot", "cushion", "cushion_bps",
               "atr4", "coa", "vol_regime", "dog_side", "dog_ask", "seconds_left", "action", "paper"]
TRADE_FIELDS = ["timestamp", "market_ts", "slug", "dog_side", "coa", "cushion_bps", "atr4",
                 "atr4_med24h", "vol_regime", "dog_ask_signal", "entry_fill_price", "shares",
                 "usd_cost", "exit_ask", "shares_target_sell", "touched", "touch_seconds_left",
                 "max_bid_after_touch", "sell_filled", "shares_sold", "shares_held",
                 "result", "resolved_winner", "dog_won", "pnl_scalp_usd", "pnl_hold_usd",
                 "pnl_total_usd", "paper"]

# ============================================================================
# 🌙 MOON DEV - SESSION STATS
# ============================================================================
SESSION_START = time.time()
S = {"entries": 0, "no_signal": 0, "ask_out_of_band": 0, "too_late": 0, "no_fill": 0,
     "max_concurrent": 0, "daily_pnl": 0.0, "day": None, "halted": False}
SPINNER = itertools.cycle(["◐", "◓", "◑", "◒"])
EVENT_LOG = deque(maxlen=9)


def log_event(msg, color="white"):
    EVENT_LOG.append((datetime.now().strftime("%H:%M:%S"), msg, color))


# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (V1 post_order is dead — throws PolyApiException)
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


def place_taker_buy(token_id, price, size):
    """🌙 Moon Dev - Marketable GTC BUY to TAKE the dog (post_only=False, we cross).
    GTC not FAK/FOK: Polymarket 400s marketable FAK/FOK under $1 crossable (verified live)."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="BUY")
    log_event(f"🐶 TAKE dog @ ${price:.3f} x{size} (GTC taker)", "cyan")
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=False)
        return resp or {}
    except Exception as e:
        err = str(e)
        if any(t in err.lower() for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
            log_event("⚠️ buy order timed out — will verify via positions", "yellow")
            return {"status": "timeout"}
        log_event(f"❌ buy order failed: {type(e).__name__}: {e}", "red")
        return {}


def place_taker_sell(token_id, price, size):
    """🌙 Moon Dev - Marketable GTC SELL (crosses the book, used only when the
    bid has already run past our resting exit price — see place_maker_sell)."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="SELL")
    log_event(f"⚡ TAKE bid @ ${price:.3f} x{size} (book already past our ask)", "green")
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=False)
        return resp or {}
    except Exception as e:
        log_event(f"❌ taker sell failed: {type(e).__name__}: {e}", "red")
        return {}


def place_maker_sell(token_id, price, size):
    """🌙 Moon Dev - Post-only GTC SELL, resting the flip-harvest ask. If the
    post-only reject says we'd CROSS (best bid already >= our price — the
    flip already overshot our target), don't chase DOWN, take the better
    bid that's already there instead. We never lower the ask, ever."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="SELL")
    log_event(f"🪑 resting SELL @ ${price:.2f} x{size:.0f} (post_only)", "cyan")
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=True)
        return resp or {}
    except Exception as e:
        err = str(e).lower()
        if 'post-only' in err and 'cross' in err:
            bid, _, _ = get_book(token_id)
            if bid and bid >= price:
                return place_taker_sell(token_id, bid, size)
            log_event("⚠️ post-only sell rejected and no better bid seen — will retry next tick", "yellow")
            return {}
        if any(t in err for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
            log_event("⚠️ sell order timed out — will verify via positions", "yellow")
            return {"status": "timeout"}
        log_event(f"❌ sell order failed: {type(e).__name__}: {e}", "red")
        return {}


def cancel_token_orders(token_id):
    """🌙 Moon Dev - Cancel resting GTC remainders on rollover."""
    from py_clob_client_v2.clob_types import OrderMarketCancelParams
    try:
        _build_client().cancel_market_orders(OrderMarketCancelParams(asset_id=str(token_id)))
    except Exception as e:
        log_event(f"⚠️ cancel failed: {type(e).__name__}", "yellow")


def position_for_token(token_id):
    """🌙 Moon Dev - (size, avg_price) we hold of token_id on AUG14, else (0, 0)."""
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
# 🌙 MOON DEV - MARKET DATA (read, blip-tolerant)
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
    """🌙 Moon Dev - btc-updown-5m-{T} -> {"Up": token, "Down": token} via Gamma."""
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


def get_btc_mark():
    """🌙 Moon Dev - Hyperliquid BTC mark price (for live touch detection)."""
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


def get_book(token_id):
    """🌙 Moon Dev - (best_bid, best_ask, ask_depth_shares) for a token."""
    r = safe_get("https://clob.polymarket.com/book", {'token_id': token_id}, timeout=5)
    if r is None or r.status_code != 200:
        return (None, None, 0.0)
    d = r.json()
    bids, asks = d.get('bids', []), d.get('asks', [])
    best_bid = max((float(b['price']) for b in bids), default=None)
    if not asks:
        return (best_bid, None, 0.0)
    best_ask = min(float(a['price']) for a in asks)
    depth = sum(float(a.get('size', 0)) for a in asks if float(a['price']) == best_ask)
    return (best_bid, best_ask, depth)


def calc_shares(usd, price):
    """🌙 Moon Dev - shares for the stake, floored to 2dp, min 5 shares."""
    shares = math.floor((usd / price) * 100) / 100
    return max(float(MIN_SHARES), shares)


# ============================================================================
# 🌙 MOON DEV - LIVE COA SIGNAL (same math as box_builder: real 1m bars, Binance->Coinbase)
# ============================================================================
def fetch_window_bars(window_ts):
    """🌙 Moon Dev - real 1-min BTC OHLC for this window (Binance, Coinbase fallback). {} on fail."""
    bars = {}
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={'symbol': 'BTCUSDT', 'interval': '1m',
                             'startTime': window_ts * 1000, 'endTime': (window_ts + 299) * 1000},
                     timeout=10)
    if r.status_code == 200:
        for k in r.json():
            off = int((k[0] / 1000 - window_ts) // 60)
            if 0 <= off <= 4:
                bars[off] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]))
    if not bars:
        r = requests.get("https://api.exchange.coinbase.com/products/BTC-USD/candles",
                         params={'granularity': 60,
                                 'start': datetime.utcfromtimestamp(window_ts).isoformat(),
                                 'end': datetime.utcfromtimestamp(window_ts + 299).isoformat()},
                         timeout=10)
        if r.status_code == 200:
            for c in r.json():
                off = int((int(c[0]) - window_ts) // 60)
                if 0 <= off <= 4:
                    bars[off] = (float(c[3]), float(c[2]), float(c[1]), float(c[4]))
    return bars


def compute_live_coa(window_ts):
    """🌙 Moon Dev - coa from bars 0-3 (all 4 closed by T-60): coa = |cushion|/ATR4.
    cushion = last-closed-bar close minus the window open. None = no data yet."""
    bars = fetch_window_bars(window_ts)
    done = sorted(o for o in bars if o <= 3)
    if 0 not in bars or len(done) < 4:
        return None
    open_p = bars[0][0]
    cushion = bars[max(done)][3] - open_p
    atr4 = sum(bars[o][1] - bars[o][2] for o in done) / len(done)
    if atr4 <= 0 or open_p <= 0:
        return None
    return {'coa': abs(cushion) / atr4, 'cushion': cushion, 'atr4': atr4,
            'cushion_bps': abs(cushion) / open_p * 10000.0,
            'favored': 'Up' if cushion > 0 else 'Down'}


# ============================================================================
# 🌙 MOON DEV - ROLLING 24H ATR4 MEDIAN (for the vol-regime lever)
# ============================================================================
def load_atr_history():
    if not os.path.exists(ATR_HISTORY_FILE):
        return []
    try:
        with open(ATR_HISTORY_FILE) as f:
            return json.load(f)
    except (ValueError, OSError):
        return []


def record_atr(window_ts, atr4):
    """🌙 Moon Dev - append this window's ATR4, trim to the trailing 24h, save."""
    hist = load_atr_history()
    hist.append({"ts": window_ts, "atr4": atr4})
    cutoff = window_ts - ATR_HISTORY_SEC
    hist = [h for h in hist if h["ts"] >= cutoff]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ATR_HISTORY_FILE, "w") as f:
        json.dump(hist, f)
    return hist


def atr_median(hist):
    """🌙 Moon Dev - rolling 24h ATR4 median, None until we have enough samples."""
    vals = [h["atr4"] for h in hist if h.get("atr4")]
    if len(vals) < ATR_MIN_SAMPLES:
        return None
    return statistics.median(vals)


def vol_regime(atr4, med):
    """🌙 Moon Dev - HIGH (harvest 100%) unless we have a trusted median AND
    we're clearly below it (harvest 50%, hold half — touches stick there)."""
    if med is None:
        return "HIGH"  # not enough history yet — default to the fully-proven-safe leg
    return "HIGH" if atr4 >= med else "LOW"


# ============================================================================
# 🌙 MOON DEV - CSV LOGGING (eval: every window. trades: fills, honest resolution)
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


def pending_count():
    return sum(1 for r in read_csv_rows(TRADES_FILE) if r["result"] == "PENDING")


def record_trade(st, window_ts):
    """🌙 Moon Dev - a fresh entry fill enters the trades file as PENDING."""
    append_csv(TRADES_FILE, TRADE_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "market_ts": window_ts, "slug": st["slug"], "dog_side": st["dog_side"],
        "coa": f"{st['coa']:.4f}", "cushion_bps": f"{st['cushion_bps']:.3f}",
        "atr4": f"{st['atr4']:.2f}", "atr4_med24h": "" if st["atr4_med24h"] is None else f"{st['atr4_med24h']:.2f}",
        "vol_regime": st["vol_regime"], "dog_ask_signal": f"{st['dog_ask_signal']:.4f}",
        "entry_fill_price": f"{st['entry_price']:.4f}", "shares": f"{st['shares']:.2f}",
        "usd_cost": f"{st['entry_price'] * st['shares']:.2f}", "exit_ask": f"{st['exit_ask']:.2f}",
        "shares_target_sell": f"{st['shares_to_sell']:.2f}", "touched": int(st["touched"]),
        "touch_seconds_left": "" if st["touch_time_left"] is None else st["touch_time_left"],
        "max_bid_after_touch": "" if st["max_bid_after_touch"] is None else f"{st['max_bid_after_touch']:.4f}",
        "sell_filled": 0, "shares_sold": "0.00", "shares_held": f"{st['shares']:.2f}",
        "result": "PENDING", "resolved_winner": "", "dog_won": "",
        "pnl_scalp_usd": "", "pnl_hold_usd": "", "pnl_total_usd": "", "paper": int(PAPER_MODE)})


def finalize_trade_row(window_ts, shares_sold, shares_held, touched, touch_left, max_bid):
    """🌙 Moon Dev - at rollover: freeze the sell outcome into the trade's PENDING row."""
    rows = read_csv_rows(TRADES_FILE)
    changed = False
    for r in rows:
        if r["market_ts"] == str(window_ts) and r["result"] == "PENDING":
            r["sell_filled"] = int(shares_sold > 0)
            r["shares_sold"] = f"{shares_sold:.2f}"
            r["shares_held"] = f"{shares_held:.2f}"
            r["touched"] = int(touched)
            r["touch_seconds_left"] = "" if touch_left is None else touch_left
            r["max_bid_after_touch"] = "" if max_bid is None else f"{max_bid:.4f}"
            changed = True
    if changed:
        write_csv_rows(TRADES_FILE, TRADE_FIELDS, rows)


# ============================================================================
# 🌙 MOON DEV - HONEST RESOLUTION (definitive only — no phantom PENDING wins)
# ============================================================================
def get_definitive_winner(market_ts, slug):
    """🌙 Moon Dev - 'Up'/'Down' only when DEFINITIVELY resolved:
    1) crypto-price completed==true -> close vs open (Chainlink rule).
    2) Gamma outcomePrices exactly 1/0 as fallback. None while unresolved."""
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
    """🌙 Moon Dev - poll PENDING trades every cycle; stamp scalp/hold/total pnl
    when definitive. Scalp leg is realized regardless of outcome; hold leg
    depends on whether the dog actually won."""
    if not os.path.exists(TRADES_FILE):
        return
    rows = read_csv_rows(TRADES_FILE)
    changed = False
    for r in rows:
        if r["result"] != "PENDING":
            continue
        close_ts = int(r["market_ts"]) + MARKET_DURATION
        if time.time() < close_ts + RESOLVE_GRACE_SEC:
            continue
        winner = get_definitive_winner(int(r["market_ts"]), r["slug"])
        if winner is None:
            continue  # stays PENDING, never faked — retried next cycle
        dog_won = r["dog_side"] == winner
        entry, exit_ask = float(r["entry_fill_price"]), float(r["exit_ask"])
        shares_sold, shares_held = float(r["shares_sold"]), float(r["shares_held"])
        pnl_scalp = shares_sold * (exit_ask - entry)
        pnl_hold = shares_held * (1.0 - entry) if dog_won else -shares_held * entry
        pnl_total = pnl_scalp + pnl_hold
        r["result"] = "WIN" if pnl_total >= 0 else "LOSS"
        r["resolved_winner"] = winner
        r["dog_won"] = int(dog_won)
        r["pnl_scalp_usd"] = f"{pnl_scalp:+.2f}"
        r["pnl_hold_usd"] = f"{pnl_hold:+.2f}"
        r["pnl_total_usd"] = f"{pnl_total:+.2f}"
        changed = True
        S["daily_pnl"] += pnl_total
        log_event(f"{'🏆' if pnl_total >= 0 else '💀'} {r['dog_side']} vs {winner} "
                  f"scalp {pnl_scalp:+.2f} hold {pnl_hold:+.2f} = {pnl_total:+.2f} {r['slug']}",
                  "green" if pnl_total >= 0 else "red")
    if changed:
        write_csv_rows(TRADES_FILE, TRADE_FIELDS, rows)


# ============================================================================
# 🌙 MOON DEV - WINDOW STATE
# ============================================================================
def fresh_state():
    return {"slug": None, "tokens": None, "strike": None, "spot": None,
            "cushion": None, "cushion_bps": None, "atr4": None, "atr4_med24h": None,
            "coa": None, "vol_regime": None, "favored": None,
            "dog_side": None, "dog_token_id": None, "dog_ask_signal": None,
            "entered": False, "placed": False, "shares": None, "entry_price": None,
            "action": None, "why": "", "last_logged_action": None,
            "exit_ask": None, "shares_to_sell": None, "sell_placed": False,
            "touched": False, "touch_time_left": None, "max_bid_after_touch": None,
            "shares_sold": 0.0, "shares_held": 0.0,
            "last_check": 0.0, "last_monitor": 0.0}


def note_skip(st, action, detail):
    st["action"] = action
    st["why"] = detail
    if st["last_logged_action"] != action:
        st["last_logged_action"] = action
        log_event(detail, "yellow")


def finalize_window(st, window_ts):
    """🌙 Moon Dev - one eval row per window (entries AND skips) — the re-mining dataset."""
    if st["slug"] is None:
        return
    action = st["action"] or "NO_SIGNAL"
    if action == "TOO_LATE":
        S["too_late"] += 1
    elif action == "NO_SIGNAL":
        S["no_signal"] += 1
    elif action in ("ASK_OUT_OF_BAND", "NO_FILL"):
        S["ask_out_of_band"] += 1
    elif action == "MAX_CONCURRENT":
        S["max_concurrent"] += 1
    append_csv(EVAL_LOG, EVAL_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "window_ts": window_ts, "slug": st["slug"],
        "strike": "" if st["strike"] is None else f"{st['strike']:.2f}",
        "spot": "" if st["spot"] is None else f"{st['spot']:.2f}",
        "cushion": "" if st["cushion"] is None else f"{st['cushion']:.2f}",
        "cushion_bps": "" if st["cushion_bps"] is None else f"{st['cushion_bps']:.3f}",
        "atr4": "" if st["atr4"] is None else f"{st['atr4']:.2f}",
        "coa": "" if st["coa"] is None else f"{st['coa']:.4f}",
        "vol_regime": st["vol_regime"] or "", "dog_side": st["dog_side"] or "",
        "dog_ask": "" if st["dog_ask_signal"] is None else f"{st['dog_ask_signal']:.4f}",
        "seconds_left": "", "action": action, "paper": int(PAPER_MODE)})


# ============================================================================
# 🌙 MOON DEV - THE HUNT (entry gate: coa + dog ask band, T-60 -> T-5)
# ============================================================================
def hunt(st, window_ts, time_left):
    if st["entered"] or st["placed"]:
        return
    if st["tokens"] is None or st["strike"] is None:
        st["why"] = "waiting for market/strike..."
        return
    if time_left > ENTRY_TIME_BAND[1]:
        st["why"] = f"⏳ {time_left}s left — waiting for bars 0-3 to close (T-{ENTRY_TIME_BAND[1]})"
        return
    if time_left < ENTRY_TIME_BAND[0]:
        note_skip(st, "TOO_LATE", f"⏰ only {time_left}s left (< {ENTRY_TIME_BAND[0]}s) — too late to fill+bracket")
        return

    now = time.time()
    if now - st["last_check"] < CHECK_INTERVAL:
        return
    st["last_check"] = now

    if S["halted"]:
        st["why"] = "🛑 daily stop hit — watching only"
        return
    if pending_count() >= MAX_CONCURRENT_PENDING:
        note_skip(st, "MAX_CONCURRENT", f"🧱 {MAX_CONCURRENT_PENDING} windows already PENDING — skip")
        return

    sig = compute_live_coa(window_ts)
    if sig is None:
        st["why"] = "📊 waiting for 4 real 1m bars (Binance/Coinbase)..."
        return
    st["cushion"], st["atr4"] = sig["cushion"], sig["atr4"]
    st["cushion_bps"], st["coa"], st["favored"] = sig["cushion_bps"], sig["coa"], sig["favored"]

    if st["coa"] > COA_MAX or st["cushion_bps"] > CUSHION_BPS_MAX:
        note_skip(st, "NO_SIGNAL",
                  f"💤 coa {st['coa']:.2f} (>{COA_MAX}) or cushion {st['cushion_bps']:.2f}bps "
                  f"(>{CUSHION_BPS_MAX}) — not a genuine coin flip, skip")
        return

    st["dog_side"] = "Down" if st["favored"] == "Up" else "Up"
    st["dog_token_id"] = st["tokens"].get(st["dog_side"])
    bid, ask, depth = get_book(st["dog_token_id"])
    if ask is None:
        st["why"] = f"📖 dog={st['dog_side']} coa {st['coa']:.2f} — empty book, re-checking"
        return
    st["dog_ask_signal"] = ask

    if not (DOG_ASK_BAND[0] <= ask <= DOG_ASK_BAND[1]):
        note_skip(st, "ASK_OUT_OF_BAND",
                  f"⏭️ dog {st['dog_side']} ask ${ask:.3f} outside {DOG_ASK_BAND[0]}-{DOG_ASK_BAND[1]} — skip")
        return

    shares = calc_shares(USD_SIZE, ask)
    if depth < shares:
        note_skip(st, "NO_FILL", f"⏭️ dog ask ${ask:.3f} in band but depth {depth:.0f} < {shares:.0f} — thin book")
        return

    st["why"] = f"🚀 dog {st['dog_side']} ask ${ask:.3f} coa {st['coa']:.2f} — FIRING"
    fire(st, window_ts, ask, shares)


def fire(st, window_ts, signal_ask, shares):
    hist = record_atr(window_ts, st["atr4"])
    st["atr4_med24h"] = atr_median(hist)
    st["vol_regime"] = vol_regime(st["atr4"], st["atr4_med24h"])
    sell_pct = HIGH_VOL_SELL_PCT if st["vol_regime"] == "HIGH" else LOW_VOL_SELL_PCT
    st["exit_ask"] = EXIT_ASK_A if (not AB_TEST_ENABLED or (window_ts // MARKET_DURATION) % 2 == 0) else EXIT_ASK_B

    if PAPER_MODE:
        st["placed"] = True
        st["entered"] = True
        st["shares"] = shares
        st["entry_price"] = signal_ask
        st["shares_to_sell"] = round(shares * sell_pct, 2)
        st["action"] = "FILLED"
        S["entries"] += 1
        record_trade(st, window_ts)
        log_event(f"📝 PAPER FILL dog {st['dog_side']} @ ${signal_ask:.3f} x{shares:.0f} "
                  f"({st['vol_regime']} vol, sell {sell_pct*100:.0f}% @ {st['exit_ask']})", "green")
        st["sell_placed"] = True  # nothing to actually rest in paper mode
        return

    st["placed"] = True
    resp = place_taker_buy(st["dog_token_id"], signal_ask, shares)
    time.sleep(1.5)
    held, avg = position_for_token(st["dog_token_id"])
    status = str(resp.get("status", "")).lower()
    filled = held > 0 or "matched" in status or float(resp.get("takingAmount", 0) or 0) > 0
    if not filled:
        st["action"] = "NO_FILL"
        log_event(f"😕 no fill @ ${signal_ask:.3f} — missed the cross", "yellow")
        return

    entry = avg if held > 0 and avg > 0 else signal_ask
    got = held if held > 0 else shares
    st["entered"] = True
    st["shares"] = got
    st["entry_price"] = entry
    st["shares_to_sell"] = max(MIN_SHARES if sell_pct < 1.0 else 0.0, round(got * sell_pct, 2))
    st["shares_to_sell"] = min(st["shares_to_sell"], got)
    st["action"] = "FILLED"
    S["entries"] += 1
    record_trade(st, window_ts)
    log_event(f"✅ FILLED dog {st['dog_side']} @ ${entry:.3f} x{got:.0f} "
              f"({st['vol_regime']} vol, sell {st['shares_to_sell']:.0f}/{got:.0f} @ {st['exit_ask']})", "green")

    place_maker_sell(st["dog_token_id"], st["exit_ask"], st["shares_to_sell"])
    st["sell_placed"] = True


# ============================================================================
# 🌙 MOON DEV - MONITOR (post-entry: touch detection + sell-fill polling)
# ============================================================================
def monitor(st):
    """🌙 Moon Dev - after a fill: track whether the dog TOUCHES the strike and
    how high the book prints after (the untested link this bot exists to
    measure), and poll our on-chain position for the resting sell's fill."""
    if not st["entered"] or PAPER_MODE:
        return
    now = time.time()
    if now - st["last_monitor"] < CHECK_INTERVAL:
        return
    st["last_monitor"] = now

    if st["spot"] is not None and st["strike"] is not None:
        crossed = (st["spot"] >= st["strike"]) if st["dog_side"] == "Up" else (st["spot"] <= st["strike"])
        if crossed and not st["touched"]:
            st["touched"] = True
            time_left = MARKET_DURATION - (int(now) - (get_current_window_ts()))
            st["touch_time_left"] = time_left
            log_event(f"⚡ TOUCH! dog {st['dog_side']} caught the strike, {time_left}s left", "magenta")
        if st["touched"]:
            bid, _, _ = get_book(st["dog_token_id"])
            if bid is not None:
                st["max_bid_after_touch"] = bid if st["max_bid_after_touch"] is None else max(st["max_bid_after_touch"], bid)

    held, _ = position_for_token(st["dog_token_id"])
    sold_so_far = max(0.0, st["shares"] - held)
    if sold_so_far > st["shares_sold"] + 0.01:
        st["shares_sold"] = min(sold_so_far, st["shares_to_sell"])
        log_event(f"💰 FLIP HARVESTED! sold {st['shares_sold']:.0f} @ ~${st['exit_ask']:.2f}", "green")
    st["shares_held"] = max(0.0, st["shares"] - st["shares_sold"])


# ============================================================================
# 🌙 MOON DEV - DASHBOARD
# ============================================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def draw_tracker():
    rows = [r for r in read_csv_rows(TRADES_FILE)]
    print(colored(f"  ========== 🌙 MOON DEV'S FLIP HARVESTER TRACKER ({len(rows)} entries) 🌙 ==========", "yellow", attrs=['bold']))
    for r in rows[-15:]:
        if r["result"] == "WIN":
            res, rcol = f"WIN  {float(r['pnl_total_usd']):+.2f}", "green"
        elif r["result"] == "LOSS":
            res, rcol = f"LOSS {float(r['pnl_total_usd']):+.2f}", "red"
        else:
            res, rcol = "PENDING", "yellow"
        touch = "✓touch" if r.get("touched") == "1" else "no-touch"
        print(colored(f"  {r['timestamp'][5:16]} | {r['dog_side']:<4} @ {float(r['entry_fill_price']):.2f} | "
                      f"sold {r['shares_sold']}/{r['shares']} | {touch:<8} | ", "white")
              + colored(f"{res:<14}", rcol, attrs=['bold'])
              + colored(f"| https://polymarket.com/event/{r['slug']}", "cyan"))
    wins = sum(1 for r in rows if r["result"] == "WIN")
    losses = sum(1 for r in rows if r["result"] == "LOSS")
    pend = sum(1 for r in rows if r["result"] == "PENDING")
    pnl = sum(float(r["pnl_total_usd"]) for r in rows if r["result"] in ("WIN", "LOSS"))
    touched_n = sum(1 for r in rows if r.get("touched") == "1")
    sold_n = sum(1 for r in rows if r.get("sell_filled") == "1")
    wr = wins / (wins + losses) * 100 if (wins + losses) else 0
    pcol = "green" if pnl >= 0 else "red"
    print(colored(f"  📊 W {wins} | L {losses} | PENDING {pend} | WR {wr:.1f}% | ", "white", attrs=['bold'])
          + colored(f"P&L ${pnl:+.2f}", pcol, attrs=['bold']))
    print(colored(f"  🔬 touched {touched_n}/{len(rows)} | sell filled {sold_n}/{len(rows)} "
                  f"(the two numbers that decide if 62c is real)", "cyan"))
    print(colored("  ================================================================================", "yellow", attrs=['bold']))


def draw(st, time_left):
    clear_screen()
    draw_tracker()
    mode = colored("🟡 PAPER (unproven)", "yellow", attrs=['bold']) if PAPER_MODE else colored("🔴 LIVE · $10 real", "red", attrs=['bold'])
    print(colored("\n  💎 MOON DEV's FLIP HARVESTER — harvest the flip, don't pray for the close 💎  ", "cyan", attrs=['bold']) + mode + "\n")
    print(colored(f"  entries {S['entries']:<3} skips: no-sig {S['no_signal']:<4} band {S['ask_out_of_band']:<3} "
                  f"late {S['too_late']:<3} stacked {S['max_concurrent']:<3}"
                  f"{' 🛑 DAILY STOP' if S['halted'] else ''}", "white"))
    mins, secs = max(0, time_left) // 60, max(0, time_left) % 60
    in_band = ENTRY_TIME_BAND[0] <= time_left <= ENTRY_TIME_BAND[1]
    print(colored(f"\n  ⏱  time left {mins}:{secs:02d}  ({'IN ENTRY BAND 🎯' if in_band else ('too late' if time_left < ENTRY_TIME_BAND[0] else 'waiting for bars')})",
                  "green" if in_band else "yellow", attrs=['bold']))
    if st["strike"] is None or st["spot"] is None:
        print(colored(f"\n  BTC {next(SPINNER)} waiting for strike/spot...", "white"))
    else:
        print(colored(f"\n  BTC strike {st['strike']:>12,.2f}  spot {st['spot']:>12,.2f}", "cyan"))
        if st["coa"] is not None:
            print(colored(f"  coa {st['coa']:.2f} (gate <= {COA_MAX}) | cushion {st['cushion_bps']:.2f}bps "
                          f"(gate <= {CUSHION_BPS_MAX}) | dog {st['dog_side']}", "white"))
        if st["entered"]:
            status = colored(f"  ✅ IN dog {st['dog_side']} x{st['shares']:.0f} @ ${st['entry_price']:.3f} — "
                             f"{'TOUCHED' if st['touched'] else 'watching for touch'} | "
                             f"sold {st['shares_sold']:.0f}/{st['shares_to_sell']:.0f} @ ${st['exit_ask']:.2f}",
                             "green", attrs=['bold'])
            print(status)
        elif st["action"]:
            print(colored(f"  [{st['action']}]", "yellow"))
        if not st["entered"] and st["why"]:
            wcol = "green" if st["why"].startswith("🚀") else ("yellow" if st["action"] else "white")
            print(colored(f"    ↳ {st['why']}", wcol, attrs=['bold'] if st["action"] else None))
    print()
    print(colored("  ┌───────────────────────── 📜 RECENT ─────────────────────────┐", "white"))
    for ts, msg, color in EVENT_LOG:
        print(colored((f"  │ {ts} {msg}")[:64].ljust(64) + "│", color))
    print(colored("  └──────────────────────────────────────────────────────────────┘", "white"))
    up = (time.time() - SESSION_START) / 60
    print(colored(f"\n  🌙 coa<={COA_MAX} | dog {DOG_ASK_BAND[0]}-{DOG_ASK_BAND[1]} | exit {EXIT_ASK_A}/{EXIT_ASK_B} A-B | "
                  f"${USD_SIZE} | up {up:.1f}m — Ctrl+C", "magenta"))


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
    log_event("💎 Flip Harvester spinning up" + (" (PAPER — unproven idea)" if PAPER_MODE else " — LIVE on AUG14"), "cyan")
    log_event(f"🎯 coa<={COA_MAX} | dog {DOG_ASK_BAND[0]}-{DOG_ASK_BAND[1]} | exit {EXIT_ASK_A}/{EXIT_ASK_B} | ${USD_SIZE}", "cyan")

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
            log_event(f"🛑 DAILY STOP {S['daily_pnl']:+.2f} <= {DAILY_STOP_LOSS} — no new entries today", "red")

        resolve_pending_trades()

        if window_ts != current_window:
            if current_window is not None:
                if not PAPER_MODE and st["placed"] and st["dog_token_id"]:
                    cancel_token_orders(st["dog_token_id"])
                    time.sleep(1.0)
                    monitor(st)  # one last poll to capture any fill right up to close
                    finalize_trade_row(current_window, st["shares_sold"], st["shares_held"],
                                        st["touched"], st["touch_time_left"], st["max_bid_after_touch"])
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
            bars = fetch_window_bars(window_ts)
            if 0 in bars:
                st["strike"] = bars[0][0]
                log_event(f"🎯 strike {st['strike']:,.2f}", "cyan")

        if now - last_mark >= 1.0:
            last_mark = now
            px = get_btc_mark()
            if px:
                st["spot"] = px

        hunt(st, window_ts, time_left)
        monitor(st)

        if now - last_draw >= 0.5:
            last_draw = now
            draw(st, time_left)
        time.sleep(0.3)


if __name__ == "__main__":
    tag = "PAPER MODE — unproven idea, logging would-be fills" if PAPER_MODE else "LIVE on AUG14 (real money, $10)"
    print(colored(f"🌙 Moon Dev's Flip Harvester — {tag}...", "cyan", attrs=['bold']))
    print(colored(f"📒 trades: {TRADES_FILE}\n📒 eval:   {EVAL_LOG}", "cyan"))
    time.sleep(0.4)
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print()
            print(colored("  ╔════════════════════════════════════════════════════╗", "yellow", attrs=['bold']))
            print(colored("  ║  💎 MOON DEV — FLIP HARVESTER STOPPED 💎             ║", "yellow", attrs=['bold']))
            print(colored("  ╚════════════════════════════════════════════════════╝", "yellow", attrs=['bold']))
            print(colored(f"  🎯 entries {S['entries']}  day P&L ${S['daily_pnl']:+.2f}", "green", attrs=['bold']))
            print(colored(f"  💾 {TRADES_FILE}", "cyan"))
            print(colored("  🌙 Moon Dev out — never lower the ask below 0.60.", "magenta"))
            print()
            break
        except Exception as e:
            print(colored(f"  💥 crashed: {type(e).__name__}: {e} — restarting in 3s", "red", attrs=['bold']))
            _CLIENT_CACHE = None
            time.sleep(3)
            continue
