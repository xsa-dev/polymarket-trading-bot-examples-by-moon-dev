"""
================================================================================
💣 MOON DEV's LIQ CASCADE TRIGGER v1.0   (aug_10th_cascade)
================================================================================
THE IDEA (Moon Dev's spec, 2026-08-10):

  1. ARM  — watch api.moondev.com/api/positions.json for a BTC position that is
            within 0.5% of its liquidation price AND is worth over $100,000.
            Whichever SIDE has the closest such position tells us where price is
            headed, because that is the fuel pile price is nearest to:

                closest big LONG near liq   -> price gets pulled DOWN  -> buy Down
                closest big SHORT near liq  -> price gets pushed UP    -> buy Up

  2. TRIGGER — do NOT trade on the arm alone. Sit and wait until somebody on that
            SAME side actually gets liquidated for at least $5,000 (a single
            liquidation print off api.moondev.com/api/all_liquidations). That
            print is the cascade starting to roll toward the big guy.

  3. FIRE  — the instant that print lands, take the ask on the 5-minute BTC
            up/down market in that direction, $5 flat.

  4. HOLD  — there is no exit. We hold to expiration. Win or lose, it resolves.

BTC ONLY. Most 5-minute windows this bot does NOTHING — it just sits there armed
(or not armed) and waits. That is the design, not a bug.

--------------------------------------------------------------------------------
EVERY KNOB MOON DEV ASKED TO BE A VARIABLE (see the CONFIG block below)
--------------------------------------------------------------------------------
  NEAR_LIQ_PCT       = 0.5      how close to liquidation the big position must be
  MIN_POSITION_USD   = 100_000  how big that near-liq position must be
  TRIGGER_LIQ_USD    = 5_000    the size of the liquidation print that triggers us
  TRIGGER_LOOKBACK   = 120s     how recent that print must be ("JUST got liq'd")
  USD_SIZE           = 5        flat stake
  MAX_ENTRY_PRICE    = 0.95     never pay above this (no edge left up there)

HONEST NOTES (real data or no bet, never faked):
  • positions.json refreshes irregularly (measured 0s-18min on 2026-07-29). We
    log the age of every snapshot and refuse to arm off data older than
    SNAPSHOT_MAX_AGE_SEC.
  • the liquidation feed is a 10-minute rolling window of individual prints
    across binance/bybit/okx/hyperliquid, each with its own ms timestamp. We
    only accept a print whose OWN timestamp is inside TRIGGER_LOOKBACK_SEC, and
    each print can only ever fire us once (dedup by exchange+ts+value).
  • one entry per 5-minute window, max. No averaging in, no exit, no hedge.
  • NO BACKTEST EXISTS for this — there is no historical archive of near-liq
    snapshots. Every window (fire or skip) writes a row to the eval CSV so this
    becomes minable later.

--------------------------------------------------------------------------------
RUNNING IT (read the README first, then read this file)
--------------------------------------------------------------------------------
📦 SELF-CONTAINED: one file. The only pip dependency for LIVE trading is
   `py_clob_client_v2` (V1's post_order is dead, it throws PolyApiException).
   `websockets` is only needed if you set LIQ_SOURCE to "ws"/"auto". Everything
   else is Python stdlib + requests — the dotenv and termcolor bits are inlined.

🔑 SECRETS: this file contains NO keys and never will. It reads them from a .env
   that it finds by walking UP from this folder (see find_and_load_env). Copy
   .env_example to .env and fill in YOUR OWN. .env is gitignored. Keep it that way.
   You need: PRIVATE_KEY{SUFFIX}, PUBLIC_KEY{SUFFIX}, MOONDEV_API_KEY.

⚙️  ADAPT IT: ACCOUNT_SUFFIX is "_AUG14" because that's Moon Dev's account naming.
   Change it to match your own .env. SIGNATURE_TYPE 2 = Gnosis Safe proxy wallet;
   use 0 for a plain EOA. The tflow conda re-exec below is a no-op on your machine.

⚠️  NOT FINANCIAL ADVICE. NOT PLUG-AND-PLAY. NO BACKTEST EXISTS for this idea (see
   README). It ships in PAPER_MODE. You can and will lose money. Your call, your risk.

Built by Moon Dev 🌙 — arm on the whale, fire on the cascade. 🚀
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
import csv
import math
import ssl
import asyncio
import threading
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
# 🌙 MOON DEV - PATH / ENV / ACCOUNT (change ACCOUNT_SUFFIX to match YOUR .env)
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_and_load_env():
    """🌙 Moon Dev - walk UP from this file until we find a .env.

    Self-contained on purpose: it does not care where you cloned the repo, how
    deep this bot sits, or what your username is. Drop a .env anywhere at or
    above this folder and it gets picked up. Returns the path it used, or None."""
    d = BOT_DIR
    for _ in range(6):
        candidate = os.path.join(d, '.env')
        if os.path.exists(candidate):
            load_env(candidate)
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


ENV_PATH = find_and_load_env()

ACCOUNT_SUFFIX = "_AUG14"   # 🔑 must match the key names in YOUR .env
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 2  # 2 = Gnosis Safe proxy wallet. Use 0 for a plain EOA.
MOONDEV_API_KEY = os.getenv("MOONDEV_API_KEY")

# ============================================================================
# 💣 MOON DEV - CONFIG: EVERY NUMBER IN THE SPEC IS A VARIABLE, RIGHT HERE
# ============================================================================
BOT_NAME = "NEAR-LIQ TRIGGER"
BOT_EMOJI = "💣"
CSV_PREFIX = "near_liq_trigger"

COIN = "BTC"                  # 🪙 BTC ONLY — this bot never touches another coin

# ---- STEP 1: ARM (the big guy about to get blown out) ----------------------
NEAR_LIQ_PCT = 0.5            # 🎯 position must be within this % of its liq price
MIN_POSITION_USD = 100_000    # 🐋 ...and be worth at least this much
SNAPSHOT_MAX_AGE_SEC = 1800   # 💀 refuse to arm off a positions.json older than this

# ---- STEP 2: TRIGGER (the cascade starting) --------------------------------
TRIGGER_LIQ_USD = 5_000       # 💥 a SINGLE liquidation print of at least this size...
TRIGGER_LOOKBACK_SEC = 120    # ⏱ ...that happened within this many seconds ("just now")
TRIGGER_TIMEFRAME = "10m"     # 🌐 REST fallback feed window

# 🔌 WHERE THE LIQUIDATION PRINTS COME FROM — "api" | "ws" | "auto"
#
#   "api"  = Moon Dev API only            <-- CURRENT (Moon Dev's call, server fixed)
#   "ws"   = direct exchange sockets only
#   "auto" = sockets, falling back to the API when a socket drops
#
# HISTORY — measured 2026-08-10 BEFORE the server fix (warm-up-corrected, 293 fresh
# prints over 6 min). The REST feed lagged badly behind the exchanges:
#     okx      p50  36s   max  73s   59/59 inside 120s
#     bybit    p50  38s   max 151s   25/26 inside 120s
#     binance  p50 181s   max 340s   43/208 inside 120s   <-- 79% ARRIVED TOO LATE
# Only 43% of prints landed inside the 120s lookback, which is why the $715,542
# bybit long liq at 12:00:05 ET never fired the bot.
#
# The websocket path (same streams as Moon Dev's liqs_multi.py) delivers the
# identical print in 0.5-1.2s and is kept intact below as the fallback / A-B check.
# Run `python liq_cascade_trigger.py --lagcheck` any time to re-measure the API and
# prove the server is keeping up.
LIQ_SOURCE = "api"
WS_STALE_SEC = 90             # 🔌 a socket silent this long counts as down

# ---- STEP 3: FIRE ----------------------------------------------------------
USD_SIZE = 5                  # 💰 flat stake per entry
MAX_ENTRY_PRICE = 0.95        # 🚧 never pay more than this per share
CROSS_BUFFER = 0.01           # 🎯 limit price = ask + this, so we actually cross
MIN_TIME_LEFT = 30            # ⏰ don't fire with less than this left in the window
MIN_SHARES = 5                # Polymarket 5-share minimum per order

# ---- STEP 4: HOLD ----------------------------------------------------------
# (there is no exit — this space intentionally left blank 🌙)

# ---- risk / plumbing -------------------------------------------------------
PAPER_MODE = True             # 📝 SHIPS AS PAPER ON PURPOSE. False = real money. Read
                              #    the whole file before you flip it. Nothing here is
                              #    plug-and-play and nothing here is financial advice.
DAILY_STOP_LOSS = -30         # 🛑 halt new entries if realized day P&L hits this
KILL_MIN_TRADES = 20          # 🔫 kill switch arms after this many resolved trades
KILL_TRAILING = 40            # 🔫 ...over the trailing N resolved trades
KILL_AVG_PNL = -0.40          # 🔫 avg realized P&L/trade below this -> stop entering

POSITIONS_POLL_SEC = 5.0      # 🔁 how often we re-read positions.json
LIQ_POLL_SEC = 5.0            # 🔁 how often we re-read the liquidation prints
FILL_POLL_SEC = 3.0           # 🔁 how often we check our own position for a fill
CANCEL_AT = 10                # ✂️ cancel any unfilled taker remainder at T-this
MARKET_DURATION = 300         # 5-minute windows
RESOLVE_GRACE_SEC = 15        # first resolution poll this many sec after close

ET = timezone(timedelta(hours=-4))
DATA_DIR = os.path.join(BOT_DIR, "data")
# 🌙 Moon Dev - paper and LIVE never share a file. A paper fill showing up in the
# live tracker (W 1 / P&L +$26.56 with zero real trades) is a lie, and this bot
# does not tell those.
MODE_TAG = "_paper" if PAPER_MODE else ""
EVAL_LOG = os.path.join(DATA_DIR, f"{CSV_PREFIX}_eval{MODE_TAG}.csv")
TRADES_FILE = os.path.join(DATA_DIR, f"{CSV_PREFIX}_trades{MODE_TAG}.csv")
EVAL_FIELDS = ["timestamp", "window_ts", "slug", "spot", "snap_age_sec",
               "armed_side", "direction", "near_long_dist", "near_long_usd",
               "near_short_dist", "near_short_usd", "qual_long_n", "qual_short_n",
               "trigger_usd", "trigger_exchange", "trigger_age_sec",
               "entry_price", "action", "liq_src", "best_cand_usd",
               "best_cand_age", "why", "paper"]
TRADE_FIELDS = ["timestamp", "market_ts", "slug", "side", "direction",
                "near_pos_usd", "near_pos_dist", "near_pos_liq_price",
                "trigger_usd", "trigger_exchange", "trigger_age_sec",
                "entry_price", "shares", "usd", "result", "resolved_winner",
                "pnl_usd", "paper"]

# ============================================================================
# 🌙 MOON DEV - SESSION STATS
# ============================================================================
SESSION_START = time.time()
S = {"windows": 0, "armed_windows": 0, "fires": 0, "fills": 0, "no_arm": 0,
     "no_trigger": 0, "daily_pnl": 0.0, "day": None, "halted": False, "killed": False}
SPINNER = itertools.cycle(["◐", "◓", "◑", "◒"])
EVENT_LOG = deque(maxlen=9)
FIRED_LIQ_KEYS = deque(maxlen=2000)   # 🌙 a liquidation print can only fire us ONCE
FIRED_LIQ_SET = set()


def log_event(msg, color="white"):
    EVENT_LOG.append((datetime.now().strftime("%H:%M:%S"), msg, color))


def mark_liq_used(key):
    """🌙 Moon Dev - remember a print so the same one can't trigger us twice."""
    if len(FIRED_LIQ_KEYS) == FIRED_LIQ_KEYS.maxlen:
        FIRED_LIQ_SET.discard(FIRED_LIQ_KEYS[0])
    FIRED_LIQ_KEYS.append(key)
    FIRED_LIQ_SET.add(key)


# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (V1 post_order throws PolyApiException — it is dead!)
# ============================================================================
_CLIENT_CACHE = None


def _build_client():
    """🌙 Moon Dev - cached CLOB V2 client (keys come from YOUR .env, never this file)."""
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
    # 🌙 Moon Dev - some .env files name it SECRET{SUFFIX} instead of API_SECRET{SUFFIX}
    api_secret = os.getenv(f"API_SECRET{ACCOUNT_SUFFIX}") or os.getenv(f"SECRET{ACCOUNT_SUFFIX}")
    passphrase = os.getenv(f"PASSPHRASE{ACCOUNT_SUFFIX}")
    if api_key and api_secret and passphrase:
        client.set_api_creds(ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=passphrase))
    else:
        client.set_api_creds(client.create_or_derive_api_creds())
    _CLIENT_CACHE = client
    return client


def place_taker_buy(token_id, price, size):
    """🌙 Moon Dev - the cascade just started, we TAKE. Marketable GTC BUY, post_only=False.

    Why GTC and not FAK/FOK: Polymarket rejects marketable FAK/FOK when the
    crossable amount is < $1 — verified live in the dog sniper. Any unfilled
    remainder rests at our price and gets cancelled at T-CANCEL_AT / rollover."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="BUY")
    log_event(f"💥 FIRING taker BUY @ ${price:.2f} x{size:.2f}", "magenta")
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=False)
        return resp or {}
    except Exception as e:
        err = str(e).lower()
        if any(t in err for t in ('timeout', 'readtimeout', 'duplicated', 'request exception')):
            log_event("⚠️ order timed out — will verify via positions", "yellow")
            return {"status": "timeout"}
        log_event(f"❌ taker buy failed: {type(e).__name__}: {e}", "red")
        return {}


def cancel_token_orders(token_id):
    """🌙 Moon Dev - Cancel any unfilled taker remainder (T-CANCEL_AT / rollover)."""
    from py_clob_client_v2.clob_types import OrderMarketCancelParams
    try:
        _build_client().cancel_market_orders(OrderMarketCancelParams(asset_id=str(token_id)))
    except Exception as e:
        log_event(f"⚠️ cancel failed: {type(e).__name__}", "yellow")


def position_for_token(token_id):
    """🌙 Moon Dev - (size, avg_price) we hold of token_id, else (0, 0).
    The position IS the fill detector — no trade-history guessing."""
    pub = os.getenv(PUBLIC_KEY_ENV_NAME)
    r = safe_get("https://data-api.polymarket.com/positions",
                 {'user': pub, 'limit': 500, 'sortBy': 'CURRENT', 'sortDirection': 'DESC'})
    if r is None or r.status_code != 200:
        return (None, None)  # 🌙 None = "couldn't read", NOT "flat" — never fake a fill
    for p in r.json():
        if str(p.get('asset')) == str(token_id):
            return (float(p.get('size', 0) or 0), float(p.get('avgPrice', 0) or 0))
    return (0.0, 0.0)


# ============================================================================
# 🌙 MOON DEV - MARKET DATA (read, blip-tolerant)
# ============================================================================
def safe_get(url, params=None, timeout=10, headers=None):
    hdrs = {'User-Agent': 'Mozilla/5.0 (moondev)'}
    if headers:
        hdrs.update(headers)
    try:
        return requests.get(url, params=params, timeout=timeout, headers=hdrs)
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


def get_book(token_id):
    """🌙 Moon Dev - (best_bid, best_ask) for a token. Best = LAST array element."""
    r = safe_get("https://clob.polymarket.com/book", {'token_id': token_id}, timeout=5)
    if r is None or r.status_code != 200:
        return (None, None)
    d = r.json()
    bids, asks = d.get('bids', []), d.get('asks', [])
    return (float(bids[-1]['price']) if bids else None,
            float(asks[-1]['price']) if asks else None)


def get_btc_spot():
    """🌙 Moon Dev - Binance last (dashboard context only — the brain is the liq data)."""
    r = safe_get("https://api.binance.com/api/v3/ticker/price", {'symbol': 'BTCUSDT'})
    if r is not None and r.status_code == 200:
        p = r.json().get('price')
        if p:
            return float(p)
    return None


# ============================================================================
# 💣 MOON DEV - SIGNAL SOURCE #1: WHO IS ABOUT TO GET BLOWN OUT (the ARM)
# ============================================================================
def _recompute_dist(p, spot, side):
    """🌙 Moon Dev - distance to liquidation, measured off LIVE spot.

    positions.json refreshes irregularly (measured 17 MINUTES stale on
    2026-08-10), and its `distance_pct` was frozen at snapshot time. But
    `liq_price` never moves — only the gap to it does. So we recompute the gap
    ourselves and the stale snapshot stops lying to us.

    A gap that has gone negative means price already blew through that liq
    price: the position is gone, so we park it at 999% and it stops qualifying."""
    liq = float(p.get('liq_price', 0) or 0)
    if not spot or not liq:
        return None
    d = (spot - liq) / spot * 100.0 if side == "long" else (liq - spot) / spot * 100.0
    return d if d > 0 else 999.0


def fetch_positions_snapshot(spot=None):
    """🌙 Moon Dev - api.moondev.com/api/positions.json.

    Shape (verified live 2026-08-10): TOP-LEVEL `longs` / `shorts` = the top 50
    positions closest to liquidation per side, ALL coins mixed, min value $75k,
    priced off a live websocket. Row: address, coin, value, entry_price,
    current_price, liq_price, distance_pct, leverage, size, pnl.
    We keep coin == COIN only. Returns None on any failure — real data or no
    bet, NEVER faked."""
    r = safe_get("https://api.moondev.com/api/positions.json",
                 headers={'X-API-Key': MOONDEV_API_KEY}, timeout=10)
    if r is None or r.status_code != 200:
        return None
    d = r.json()
    longs = [p for p in d.get('longs', []) if p.get('coin') == COIN]
    shorts = [p for p in d.get('shorts', []) if p.get('coin') == COIN]
    for p in longs:
        p['_live_dist'] = _recompute_dist(p, spot, "long")
    for p in shorts:
        p['_live_dist'] = _recompute_dist(p, spot, "short")
    age = None
    try:
        upd = datetime.fromisoformat(d.get('updated_at', ''))
        age = (datetime.now(timezone.utc) - upd).total_seconds()
    except (ValueError, TypeError):
        pass
    return {"longs": longs, "shorts": shorts, "age_sec": age,
            "updated_at": d.get('updated_at', '')}


def _val(p):
    return float(p.get('value', 0) or 0)


def _dist(p):
    """🌙 Moon Dev - the live-recomputed gap when we have spot, else the feed's own."""
    d = p.get('_live_dist')
    if d is not None:
        return float(d)
    return float(p.get('distance_pct', 100) or 100)


def qualifying(positions):
    """🌙 Moon Dev - the big guys on death's door: within NEAR_LIQ_PCT AND over
    MIN_POSITION_USD. Sorted closest-to-liquidation first."""
    q = [p for p in positions if _dist(p) <= NEAR_LIQ_PCT and _val(p) >= MIN_POSITION_USD]
    return sorted(q, key=_dist)


def arm(snap):
    """🌙 Moon Dev - STEP 1. Which side of the book are we closest to blowing up?

    Returns (direction, side_label, the_position, why).
      direction "Down" -> the nearest doomed whale is a LONG (his liq is BELOW us)
      direction "Up"   -> the nearest doomed whale is a SHORT (his liq is ABOVE us)
    Nobody within NEAR_LIQ_PCT at MIN_POSITION_USD -> (None, ...) and we sit out."""
    ql = qualifying(snap["longs"])
    qs = qualifying(snap["shorts"])
    best_l = ql[0] if ql else None
    best_s = qs[0] if qs else None

    if best_l is None and best_s is None:
        return (None, None, None,
                f"😴 nobody within {NEAR_LIQ_PCT}% of liq at ≥${MIN_POSITION_USD/1e3:.0f}k — not armed")

    if best_s is None or (best_l is not None and _dist(best_l) <= _dist(best_s)):
        p = best_l
        why = (f"🎯 ARMED DOWN — ${_val(p)/1e3:.0f}k LONG only {_dist(p):.2f}% from liq "
               f"@ ${float(p.get('liq_price', 0)):,.0f} (closer than any short)")
        return ("Down", "LONG", p, why)

    p = best_s
    why = (f"🎯 ARMED UP — ${_val(p)/1e3:.0f}k SHORT only {_dist(p):.2f}% from liq "
           f"@ ${float(p.get('liq_price', 0)):,.0f} (closer than any long)")
    return ("Up", "SHORT", p, why)


# ============================================================================
# 🔌 MOON DEV - SIGNAL SOURCE #2a: THE LIVE WEBSOCKET LIQUIDATION FEED
# ----------------------------------------------------------------------------
# Straight off Binance / Bybit / OKX, the exact streams from Moon Dev's
# liqs_multi.py. A print lands here in MILLISECONDS instead of the REST feed's
# ~150s median. Runs in a daemon thread; the bot never blocks on it.
#
# SIDE MAPPING IS THE EASY THING TO GET BACKWARDS (from liqs_multi.py):
#   Binance : side SELL -> a LONG got liquidated
#   Bybit   : side BUY  -> a LONG got liquidated   (INVERTED vs binance!)
#   OKX     : posSide says "long"/"short" directly
# ============================================================================
WS_PRINTS = deque(maxlen=600)
WS_LOCK = threading.Lock()
WS_CONN = {"binance": False, "bybit": False, "okx": False}
WS_LAST_MSG = {"binance": 0.0, "bybit": 0.0, "okx": 0.0}
WS_COUNT = {"binance": 0, "bybit": 0, "okx": 0}
_WS_STARTED = False

_SSL = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE


def _ws_alive_ping(ex):
    WS_LAST_MSG[ex] = time.time()


def _ws_push(ex, is_long_liq, value, price, ts_ms):
    """🌙 Moon Dev - one real liquidation print, stamped with LOCAL arrival time."""
    if not value or value <= 0:
        return
    now = time.time()
    # 🌙 exchange clocks drift — never let a future timestamp poison the age math
    ts_ms = min(int(ts_ms), int(now * 1000))
    with WS_LOCK:
        WS_PRINTS.append({"exchange": ex, "side": "LONG" if is_long_liq else "SHORT",
                          "value": float(value), "price": float(price),
                          "ts_ms": ts_ms, "seen": now, "src": "ws"})
    WS_COUNT[ex] += 1


async def _ws_binance():
    """🌙 Moon Dev - !forceOrder@arr. The /ws/ path went silent in 2026 — /market/ws/ works."""
    import websockets
    url = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
    want = (COIN + "USDT", COIN + "USDC")
    while True:
        try:
            async with websockets.connect(url, ping_interval=10, ping_timeout=5, ssl=_SSL) as ws:
                WS_CONN["binance"] = True
                _ws_alive_ping("binance")
                async for msg in ws:
                    _ws_alive_ping("binance")
                    o = json.loads(msg).get("o")
                    if not o or o.get("s") not in want:
                        continue
                    px = float(o.get("p", 0) or 0)
                    qty = float(o.get("z", 0) or 0)
                    _ws_push("binance", str(o.get("S", "")).upper() == "SELL",
                             qty * px, px, o.get("T") or time.time() * 1000)
        except Exception:
            pass
        WS_CONN["binance"] = False
        await asyncio.sleep(5)


async def _ws_bybit():
    """🌙 Moon Dev - allLiquidation.{COIN}USDT. Bybit is INVERTED: Buy = a long died."""
    import websockets
    url = "wss://stream.bybit.com/v5/public/linear"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10, ssl=_SSL) as ws:
                await ws.send(json.dumps({"op": "subscribe",
                                          "args": [f"allLiquidation.{COIN}USDT"]}))
                WS_CONN["bybit"] = True
                _ws_alive_ping("bybit")
                async for msg in ws:
                    _ws_alive_ping("bybit")
                    d = json.loads(msg)
                    if "allLiquidation" not in str(d.get("topic", "")):
                        continue
                    for liq in d.get("data", []) or []:
                        px = float(liq.get("p", 0) or 0)
                        sz = float(liq.get("v", 0) or 0)
                        _ws_push("bybit", str(liq.get("S", "")).upper() == "BUY",
                                 sz * px, px, liq.get("T") or time.time() * 1000)
        except Exception:
            pass
        WS_CONN["bybit"] = False
        await asyncio.sleep(5)


_OKX_CT = {}


def _okx_ctval(inst):
    """🌙 Moon Dev - OKX sizes are CONTRACTS. Need ctVal or the USD math is nonsense."""
    if inst in _OKX_CT:
        return _OKX_CT[inst]
    info = {"ctVal": 1.0, "ccy": "USD"}
    r = safe_get("https://www.okx.com/api/v5/public/instruments",
                 {"instType": "SWAP", "instId": inst}, timeout=5)
    if r is not None and r.status_code == 200:
        d = r.json()
        if d.get("code") == "0" and d.get("data"):
            info = {"ctVal": float(d["data"][0].get("ctVal", 1) or 1),
                    "ccy": d["data"][0].get("ctValCcy", "")}
    _OKX_CT[inst] = info
    return info


async def _ws_okx():
    """🌙 Moon Dev - liquidation-orders on SWAP, filtered to our coin."""
    import websockets
    url = "wss://ws.okx.com:8443/ws/v5/public"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10, ssl=_SSL) as ws:
                await ws.send(json.dumps({"op": "subscribe",
                                          "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]}))
                WS_CONN["okx"] = True
                _ws_alive_ping("okx")
                async for msg in ws:
                    _ws_alive_ping("okx")
                    d = json.loads(msg)
                    for row in d.get("data", []) or []:
                        inst = row.get("instId", "")
                        if not inst.startswith(COIN + "-"):
                            continue
                        info = _okx_ctval(inst)
                        for det in row.get("details", []) or []:
                            px = float(det.get("bkPx", 0) or 0)
                            sz = float(det.get("sz", 0) or 0)
                            usd = sz * info["ctVal"] if info["ccy"] == "USD" else sz * info["ctVal"] * px
                            _ws_push("okx", str(det.get("posSide", "")).lower() == "long",
                                     usd, px, det.get("ts") or time.time() * 1000)
        except Exception:
            pass
        WS_CONN["okx"] = False
        await asyncio.sleep(5)


def start_ws_feed():
    """🌙 Moon Dev - fire up all three sockets in a daemon thread."""
    global _WS_STARTED
    if _WS_STARTED:
        return
    _WS_STARTED = True

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.gather(_ws_binance(), _ws_bybit(), _ws_okx()))

    threading.Thread(target=runner, daemon=True, name="moondev-liq-ws").start()


def ws_is_up():
    """🌙 Moon Dev - is the live feed usable?

    Judged by CONNECTION state, not last-message time: bybit only pushes on its
    one subscribed topic, so a quiet BTC hour is silent but perfectly healthy.
    websockets' ping/pong (20s/10s) drops a truly dead socket, which flips
    WS_CONN False — that is the honest signal."""
    return any(WS_CONN.values())


def ws_prints():
    """🌙 Moon Dev - live prints, newest first. Only ones we WITNESSED live count."""
    now = time.time()
    with WS_LOCK:
        rows = list(WS_PRINTS)
    out = [e for e in rows if (now - e["seen"]) <= TRIGGER_LOOKBACK_SEC * 3]
    out.sort(key=lambda e: e["ts_ms"], reverse=True)
    return out


# ============================================================================
# 💣 MOON DEV - SIGNAL SOURCE #2b: THE REST FALLBACK (slow — see LIQ_SOURCE)
# ============================================================================
def fetch_liq_prints():
    """🌙 Moon Dev - api.moondev.com/api/all_liquidations/{tf}.json.

    Shape (verified live 2026-08-10): `liquidations` = a list of INDIVIDUAL
    liquidation prints from binance/bybit/okx/hyperliquid, each
    {exchange, symbol, side (LONG/SHORT), value, price, quantity, timestamp(ms)}.
    We keep symbol == COIN only. Returns None on failure — never faked."""
    r = safe_get(f"https://api.moondev.com/api/all_liquidations/{TRIGGER_TIMEFRAME}.json",
                 headers={'X-API-Key': MOONDEV_API_KEY}, timeout=15)
    if r is None or r.status_code != 200:
        return None
    d = r.json()
    now = time.time()
    out = []
    for x in d.get('liquidations', []):
        if str(x.get('symbol', '')).upper() != COIN:
            continue
        ts_ms = x.get('timestamp')
        if not ts_ms:
            continue
        out.append({"exchange": x.get('exchange', '?'),
                    "side": str(x.get('side', '')).upper(),   # LONG / SHORT
                    "value": float(x.get('value', 0) or 0),
                    "price": float(x.get('price', 0) or 0),
                    "ts_ms": int(ts_ms), "seen": now, "src": "api"})
    out.sort(key=lambda e: e["ts_ms"], reverse=True)
    return out


def get_prints():
    """🌙 Moon Dev - the prints + which pipe they came down. Sockets win when up."""
    if LIQ_SOURCE == "api":
        return (fetch_liq_prints(), "api")
    if LIQ_SOURCE == "ws":
        return (ws_prints(), "ws")
    if ws_is_up():                        # "auto"
        return (ws_prints(), "ws")
    return (fetch_liq_prints(), "api-fallback")


def scan_for_trigger(prints, side_label):
    """🌙 Moon Dev - STEP 2. A cascade print on OUR side, big enough and fresh enough.

    side_label 'LONG'  -> we need a LONG liquidation (forced sells, price down)
    side_label 'SHORT' -> we need a SHORT liquidation (forced buys, price up)
    Each print can only ever fire us once.

    Returns (trigger_or_None, diag). `diag` always names the biggest print we saw
    on our side and WHY it wasn't good enough — so a window that doesn't fire is
    never a mystery again (this is exactly what was missing when the $715k bybit
    print went by on 2026-08-10)."""
    now_ms = time.time() * 1000
    ours = [e for e in prints if e["side"] == side_label]
    best = None
    for e in ours:
        c = dict(e)
        c["age_sec"] = (now_ms - e["ts_ms"]) / 1000.0
        if best is None or c["value"] > best["value"]:
            best = c

    for e in ours:                        # newest first — the freshest domino wins
        if e["value"] < TRIGGER_LIQ_USD:
            continue
        age = (now_ms - e["ts_ms"]) / 1000.0
        if age < 0 or age > TRIGGER_LOOKBACK_SEC:
            continue
        key = f"{e['exchange']}|{e['ts_ms']}|{e['value']}"
        if key in FIRED_LIQ_SET:
            continue
        t = dict(e)
        t["age_sec"] = age
        t["key"] = key
        return (t, {"best": best, "n_side": len(ours), "why": ""})

    if best is None:
        why = f"no {side_label} prints in the feed at all"
    elif best["value"] < TRIGGER_LIQ_USD:
        why = f"biggest {side_label} print is only ${best['value']:,.0f} (need ${TRIGGER_LIQ_USD:,})"
    elif best["age_sec"] > TRIGGER_LOOKBACK_SEC:
        why = f"biggest (${best['value']:,.0f}) is {best['age_sec']:.0f}s old (need <{TRIGGER_LOOKBACK_SEC}s)"
    else:
        why = "the only qualifying print already fired us once"
    return (None, {"best": best, "n_side": len(ours), "why": why})


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


def record_trade(st, window_ts, shares):
    """🌙 Moon Dev - we fired and got filled — enter the trades file as PENDING."""
    p, t = st["arm_pos"], st["trigger"]
    append_csv(TRADES_FILE, TRADE_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "market_ts": window_ts, "slug": st["slug"], "side": st["direction"],
        "direction": st["direction"],
        "near_pos_usd": f"{_val(p):.0f}", "near_pos_dist": f"{_dist(p):.3f}",
        "near_pos_liq_price": f"{float(p.get('liq_price', 0)):.2f}",
        "trigger_usd": f"{t['value']:.0f}", "trigger_exchange": t["exchange"],
        "trigger_age_sec": f"{t['age_sec']:.0f}",
        "entry_price": f"{st['entry_price']:.4f}",
        "shares": f"{shares:.2f}", "usd": f"{st['entry_price'] * shares:.2f}",
        "result": "PENDING", "resolved_winner": "", "pnl_usd": "",
        "paper": int(PAPER_MODE)})


# ============================================================================
# 🌙 MOON DEV - HONEST RESOLUTION (Gamma closed=true ONLY — the July 24 correction)
# ============================================================================
def get_definitive_winner(slug):
    """🌙 Moon Dev - closed=true is REQUIRED: a closed 5-min market is excluded
    from the default Gamma query and would sit PENDING forever without it.
    'Up'/'Down' only when outcomePrices are exactly 1/0 — None otherwise."""
    r = safe_get("https://gamma-api.polymarket.com/markets", {'slug': slug, 'closed': 'true'})
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
    """🌙 Moon Dev - stamp WIN/LOSS + P&L when definitive. PENDING is NEVER faked."""
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
        winner = get_definitive_winner(r["slug"])
        if winner is None:
            continue  # stays PENDING, retried forever — never faked
        won = r["side"] == winner
        shares = float(r["shares"] or 0)
        cost = float(r["entry_price"]) * shares
        pnl = (shares - cost) if won else -cost
        r["result"] = "WIN" if won else "LOSS"
        r["resolved_winner"] = winner
        r["pnl_usd"] = f"{pnl:+.2f}"
        changed = True
        S["daily_pnl"] += pnl
        tag = "🏆 WON" if won else "💀 LOST"
        log_event(f"{tag} {r['side']} vs {winner} ${pnl:+.2f} {r['slug']}",
                  "green" if won else "red")
    if changed:
        write_csv_rows(TRADES_FILE, TRADE_FIELDS, rows)


def check_kill_switch():
    """🌙 Moon Dev - trailing avg realized P&L judges the bot, nothing else."""
    if S["killed"] or not os.path.exists(TRADES_FILE):
        return
    resolved = [r for r in read_csv_rows(TRADES_FILE) if r["result"] in ("WIN", "LOSS")]
    if len(resolved) < KILL_MIN_TRADES:
        return
    tail = resolved[-KILL_TRAILING:]
    avg = sum(float(r["pnl_usd"] or 0) for r in tail) / len(tail)
    if avg < KILL_AVG_PNL:
        S["killed"] = True
        log_event(f"🔫 KILL SWITCH: trailing-{len(tail)} avg P&L ${avg:+.2f} < ${KILL_AVG_PNL:.2f} "
                  f"— no more entries", "red")


# ============================================================================
# 🌙 MOON DEV - WINDOW STATE + EVAL LOG
# ============================================================================
def fresh_state():
    return {"slug": None, "tokens": None, "spot": None, "snap": None,
            "direction": None, "arm_side": None, "arm_pos": None, "arm_why": "",
            "trigger": None, "token_id": None, "entry_price": None,
            "fired": False, "filled": False, "shares": 0.0,
            "action": None, "why": "", "last_fill_poll": 0.0,
            "counted_armed": False, "liq_src": "", "diag": None,
            "recent_prints": []}


def finalize_window(st, window_ts):
    """🌙 Moon Dev - one eval row per window (fires AND sit-outs) — the dataset."""
    if st["slug"] is None:
        return  # market never discovered — nothing real to log
    snap = st["snap"] or {"longs": [], "shorts": [], "age_sec": None}
    ql = qualifying(snap["longs"])
    qs = qualifying(snap["shorts"])
    t = st["trigger"] or {}
    p = st["arm_pos"]
    best = (st.get("diag") or {}).get("best")
    append_csv(EVAL_LOG, EVAL_FIELDS, {
        "timestamp": datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S"),
        "window_ts": window_ts, "slug": st["slug"],
        "spot": "" if st["spot"] is None else f"{st['spot']:.2f}",
        "snap_age_sec": "" if snap["age_sec"] is None else f"{snap['age_sec']:.0f}",
        "armed_side": st["arm_side"] or "", "direction": st["direction"] or "",
        "near_long_dist": f"{_dist(ql[0]):.3f}" if ql else "",
        "near_long_usd": f"{_val(ql[0]):.0f}" if ql else "",
        "near_short_dist": f"{_dist(qs[0]):.3f}" if qs else "",
        "near_short_usd": f"{_val(qs[0]):.0f}" if qs else "",
        "qual_long_n": len(ql), "qual_short_n": len(qs),
        "trigger_usd": f"{t.get('value', 0):.0f}" if t else "",
        "trigger_exchange": t.get("exchange", ""),
        "trigger_age_sec": f"{t.get('age_sec', 0):.0f}" if t else "",
        "entry_price": "" if st["entry_price"] is None else f"{st['entry_price']:.4f}",
        "action": st["action"] or ("ARMED_NO_TRIGGER" if st["direction"] else "NOT_ARMED"),
        "liq_src": st.get("liq_src", ""),
        "best_cand_usd": f"{best['value']:.0f}" if best else "",
        "best_cand_age": f"{best['age_sec']:.0f}" if best else "",
        "why": st["why"] or st["arm_why"],
        "paper": int(PAPER_MODE)})


# ============================================================================
# 💣 MOON DEV - THE ENGINE: arm on the whale, sit, fire on the cascade
# ============================================================================
def update_arm(st, now):
    """🌙 Moon Dev - STEP 1, refreshed continuously: who is closest to death?
    The arm can flip sides mid-window as the whales move — that is intended,
    it is a live read of where the fuel is RIGHT NOW."""
    if st["fired"]:
        return  # already committed this window — the arm is frozen
    if now - st.get("last_pos_poll", 0) < POSITIONS_POLL_SEC:
        return
    st["last_pos_poll"] = now
    snap = fetch_positions_snapshot(st.get("spot"))
    if snap is None:
        st["why"] = "🌐 positions.json unreachable — cannot arm"
        return
    if snap["age_sec"] is not None and snap["age_sec"] > SNAPSHOT_MAX_AGE_SEC:
        st["snap"] = snap
        st["direction"] = None
        st["why"] = f"💀 positions snapshot {snap['age_sec']:.0f}s stale (>{SNAPSHOT_MAX_AGE_SEC}s) — not arming"
        return
    st["snap"] = snap
    direction, side_label, pos, why = arm(snap)
    if direction != st["direction"]:
        log_event(why, "cyan" if direction else "white")
    st["direction"], st["arm_side"], st["arm_pos"], st["arm_why"] = direction, side_label, pos, why
    if direction and not st["counted_armed"]:
        st["counted_armed"] = True
        S["armed_windows"] += 1


def check_trigger_and_fire(st, window_ts, time_left, now):
    """🌙 Moon Dev - STEP 2 + 3: wait for the cascade print, then take the ask."""
    if st["fired"] or st["direction"] is None or st["tokens"] is None:
        return
    if now - st.get("last_liq_poll", 0) < LIQ_POLL_SEC:
        return
    st["last_liq_poll"] = now

    prints, src = get_prints()
    if prints is None:
        st["why"] = "🌐 liquidation feed unreachable — cannot trigger"
        return
    st["liq_src"] = src
    # 🌙 feed freshness, front and centre — this is the number that broke us before
    st["feed_n"] = len(prints)
    st["feed_newest_age"] = ((time.time() * 1000 - prints[0]["ts_ms"]) / 1000.0) if prints else None
    # 🌙 dashboard shows the BIGGEST prints on our side, not the most recent —
    # the most recent are always $64 dust and made the feed look broken.
    st["recent_prints"] = sorted([e for e in prints if e["side"] == st["arm_side"]],
                                 key=lambda e: -e["value"])[:4]
    trig, diag = scan_for_trigger(prints, st["arm_side"])
    st["diag"] = diag
    if trig is None:
        st["why"] = (f"⏳ armed {st['direction']} via {src} — {diag['why']}")
        return

    # 🌙 the cascade print landed — burn it so it can never fire us twice
    mark_liq_used(trig["key"])
    st["trigger"] = trig
    log_event(f"💥 TRIGGER[{src}]: ${trig['value']:,.0f} {trig['side']} liq on {trig['exchange']} "
              f"({trig['age_sec']:.0f}s ago)", "magenta")

    if time_left < MIN_TIME_LEFT:
        st["fired"] = True
        st["action"] = "TOO_LATE"
        st["why"] = f"⏰ trigger hit with only {time_left}s left (< {MIN_TIME_LEFT}s) — stood down"
        log_event(st["why"], "yellow")
        return

    if S["killed"] or S["halted"]:
        st["fired"] = True
        st["action"] = "KILLED" if S["killed"] else "HALTED"
        st["why"] = "🔫 kill switch — watching only" if S["killed"] else "🛑 daily stop — watching only"
        log_event(st["why"], "red")
        return

    token_id = st["tokens"].get(st["direction"])
    if token_id is None:
        st["why"] = f"❓ no {st['direction']} token on this market"
        return
    bid, ask = get_book(token_id)
    if ask is None:
        st["fired"] = True
        st["action"] = "NO_BOOK"
        st["why"] = f"📖 no ask on the {st['direction']} book at trigger time"
        log_event(st["why"], "yellow")
        return
    if ask > MAX_ENTRY_PRICE:
        st["fired"] = True
        st["action"] = "TOO_EXPENSIVE"
        st["why"] = f"🚧 {st['direction']} ask ${ask:.2f} > cap ${MAX_ENTRY_PRICE:.2f} — no edge left, stood down"
        log_event(st["why"], "yellow")
        return

    price = min(round(ask + CROSS_BUFFER, 2), 0.99)
    shares = math.floor((USD_SIZE / price) * 100) / 100
    shares = max(float(MIN_SHARES), shares)

    st["token_id"] = token_id
    st["entry_price"] = ask          # 🌙 we cross, so we pay the ask
    st["shares"] = shares
    st["fired"] = True
    st["action"] = "FIRED"
    S["fires"] += 1

    if PAPER_MODE:
        st["filled"] = True
        S["fills"] += 1
        record_trade(st, window_ts, shares)
        log_event(f"📝 PAPER FILL {st['direction']} @ ${ask:.2f} x{shares:.1f} — holding to expiry", "green")
        st["why"] = f"📝 paper: bought {st['direction']} @ ${ask:.2f}, holding to expiration"
        return

    place_taker_buy(token_id, price, shares)
    st["why"] = f"💥 fired {st['direction']} @ ~${ask:.2f} — confirming fill via position"


def watch_fill(st, window_ts, time_left, now):
    """🌙 Moon Dev - position size is the ground truth. No exit — a fill just holds."""
    if PAPER_MODE or not st["fired"] or st["filled"] or st["action"] != "FIRED":
        return
    if now - st["last_fill_poll"] < FILL_POLL_SEC:
        return
    st["last_fill_poll"] = now
    held, avg = position_for_token(st["token_id"])
    if held is not None and held > 0:
        st["filled"] = True
        st["shares"] = held
        if avg:
            st["entry_price"] = avg
        S["fills"] += 1
        record_trade(st, window_ts, held)
        log_event(f"⚡ FILLED {st['direction']} @ ${st['entry_price']:.2f} x{held:.1f} — holding to expiry", "green")
        st["why"] = f"⚡ in {st['direction']} x{held:.1f} @ ${st['entry_price']:.2f} — no exit, holds to expiration"
        return
    if time_left <= CANCEL_AT:
        cancel_token_orders(st["token_id"])
        st["action"] = "NO_FILL"
        st["why"] = "✂️ taker remainder never filled — cancelled at the buzzer"
        log_event(st["why"], "yellow")


# ============================================================================
# 🌙 MOON DEV - DASHBOARD
# ============================================================================
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def draw_tracker():
    """🌙 Moon Dev - clickable trade history, seeded from CSV so it survives restarts."""
    rows = read_csv_rows(TRADES_FILE)
    print(colored(f"  ========== 🌙 MOON DEV'S TRADE TRACKER ({len(rows)} fires) 🌙 ==========",
                  "yellow", attrs=['bold']))
    for r in rows[-15:]:
        if r["result"] == "WIN":
            res, rcol = f"WIN  +${float(r['pnl_usd']):.2f}", "green"
        elif r["result"] == "LOSS":
            res, rcol = f"LOSS -${abs(float(r['pnl_usd'])):.2f}", "red"
        else:
            res, rcol = "PENDING", "yellow"
        print(colored(f"  {r['timestamp'][5:16]} | {r['side']:<4} @ {float(r['entry_price']):.2f} "
                      f"| trig ${float(r['trigger_usd'])/1e3:.0f}k {r['trigger_exchange'][:4]:<4} | ", "white")
              + colored(f"{res:<13}", rcol, attrs=['bold'])
              + colored(f"| https://polymarket.com/event/{r['slug']}", "cyan"))
    wins = sum(1 for r in rows if r["result"] == "WIN")
    losses = sum(1 for r in rows if r["result"] == "LOSS")
    pend = sum(1 for r in rows if r["result"] == "PENDING")
    pnl = sum(float(r["pnl_usd"] or 0) for r in rows if r["result"] in ("WIN", "LOSS"))
    settled = wins + losses
    wr = wins / settled * 100 if settled else 0
    pcol = "green" if pnl >= 0 else "red"
    print(colored(f"  🏆 W {wins} | 💀 L {losses} | ⏳ PENDING {pend} | WR {wr:.1f}% | ",
                  "white", attrs=['bold'])
          + colored(f"P&L (resolved) ${pnl:+.2f}", pcol, attrs=['bold']))
    print(colored("  ======================================================================", "yellow", attrs=['bold']))


def draw(st, time_left):
    clear_screen()
    draw_tracker()
    mode = colored("📝 PAPER", "yellow", attrs=['bold']) if PAPER_MODE else colored("🔴 LIVE · $5 real", "red", attrs=['bold'])
    print(colored(f"\n  {BOT_EMOJI} MOON DEV's {BOT_NAME} — arm on the whale, fire on the cascade {BOT_EMOJI}  ",
                  "cyan", attrs=['bold']) + mode + "\n")
    print(colored(f"  windows {S['windows']:<4} armed {S['armed_windows']:<4} fired {S['fires']:<3} "
                  f"fills {S['fills']:<3}"
                  f"{' 🛑 DAILY STOP' if S['halted'] else ''}{' 🔫 KILLED' if S['killed'] else ''}", "white"))
    mins, secs = max(0, time_left) // 60, max(0, time_left) % 60
    spot_s = "{:,.0f}".format(st["spot"]) if st["spot"] else next(SPINNER)
    print(colored(f"\n  ⏱  time left {mins}:{secs:02d}   {COIN} spot {spot_s}", "green", attrs=['bold']))

    # ---- STEP 1: the arm ----
    snap = st["snap"]
    if snap:
        ql, qs = qualifying(snap["longs"]), qualifying(snap["shorts"])
        age_s = f"{snap['age_sec']:.0f}s" if snap["age_sec"] is not None else "?"
        print(colored(f"\n  1️⃣  NEAR-LIQ WHALES (≤{NEAR_LIQ_PCT}% away, ≥${MIN_POSITION_USD/1e3:.0f}k, age {age_s}):",
                      "white", attrs=['bold']))
        for tag, lst, col, arrow in (("LONGS ", ql, "green", "liq BELOW → Down"),
                                     ("SHORTS", qs, "red", "liq ABOVE → Up")):
            if lst:
                line = "  ".join(f"${_val(p)/1e3:.0f}k@{_dist(p):.2f}%" for p in lst[:3])
                print(colored(f"     📌 {tag} {len(lst):>2} qualify: {line}", col) + colored(f"   ({arrow})", "white"))
            else:
                print(colored(f"     ·  {tag}  none qualify", "white"))
    else:
        print(colored(f"\n  1️⃣  {next(SPINNER)} waiting for Moon Dev positions.json...", "white"))

    # ---- feed health: the sockets are the trigger, so show them ----
    src = st.get("liq_src") or LIQ_SOURCE
    if LIQ_SOURCE == "api":
        na = st.get("feed_newest_age")
        # 🌙 freshest print > 120s means the API is behind and we CANNOT fire — say so
        col = "green" if (na is not None and na <= TRIGGER_LOOKBACK_SEC) else "yellow"
        age_s = f"{na:.0f}s old" if na is not None else "none held"
        print(colored(f"\n  🔌 LIQ FEED [moon dev api]  ", "white", attrs=['bold'])
              + colored(f"{st.get('feed_n', 0)} {COIN} prints · freshest {age_s}", col))
    else:
        parts = [colored(f"{'●' if WS_CONN[ex] else '○'} {ex} {WS_COUNT[ex]}",
                         "green" if WS_CONN[ex] else "red") for ex in ("binance", "bybit", "okx")]
        print(colored(f"\n  🔌 LIQ FEED [{src}]  ", "white", attrs=['bold']) + "  ".join(parts))

    # ---- STEP 2: the trigger ----
    if st["direction"]:
        print(colored(f"\n  2️⃣  ARMED {st['direction'].upper()} — need a ${TRIGGER_LIQ_USD:,}+ {st['arm_side']} "
                      f"liquidation within {TRIGGER_LOOKBACK_SEC}s", "cyan", attrs=['bold']))
        rp = st.get("recent_prints") or []
        if rp:
            print(colored(f"       biggest {st['arm_side']} prints seen:", "white"))
        for e in rp:
            age = (time.time() * 1000 - e["ts_ms"]) / 1000.0
            hot = e["value"] >= TRIGGER_LIQ_USD and age <= TRIGGER_LOOKBACK_SEC
            print(colored(f"       {'🔥' if hot else '· '} ${e['value']:>10,.0f} {e['side']:<5} "
                          f"{e['exchange']:<9} {age:>5.0f}s ago", "magenta" if hot else "white"))
        diag = st.get("diag")
        if diag and diag.get("why"):
            print(colored(f"       ↳ no fire: {diag['why']}", "yellow"))
    else:
        print(colored(f"\n  2️⃣  NOT ARMED — sitting out this window", "white", attrs=['bold']))

    # ---- STEP 3/4: the fire + the hold ----
    if st["filled"]:
        print(colored(f"\n  3️⃣  IN {st['direction']} x{st['shares']:.1f} @ ${st['entry_price']:.2f} "
                      f"— 4️⃣ NO EXIT, holding to expiration", "green", attrs=['bold']))
    elif st["action"]:
        print(colored(f"\n  3️⃣  [{st['action']}]", "yellow", attrs=['bold']))
    if st["why"]:
        print(colored(f"    ↳ {st['why']}", "white"))
    print()
    print(colored("  ┌───────────────────────── 📜 RECENT ─────────────────────────┐", "white"))
    for ts, msg, color in EVENT_LOG:
        print(colored((f"  │ {ts} {msg}")[:64].ljust(64) + "│", color))
    print(colored("  └──────────────────────────────────────────────────────────────┘", "white"))
    up = (time.time() - SESSION_START) / 60
    print(colored(f"\n  🌙 arm: ≤{NEAR_LIQ_PCT}% & ≥${MIN_POSITION_USD/1e3:.0f}k | fire: ${TRIGGER_LIQ_USD:,} liq "
                  f"≤{TRIGGER_LOOKBACK_SEC}s | ${USD_SIZE} flat | hold to expiry | up {up:.1f}m — Ctrl+C", "magenta"))


# ============================================================================
# 📏 MOON DEV - LAGCHECK: is the API keeping up? (the tool that found the bug)
# ============================================================================
def lagcheck(minutes=6):
    """🌙 Moon Dev - measure how long a real liquidation takes to show up in the API.

    A WARM-UP POLL IS MANDATORY: everything already sitting in the 10-minute window
    is backlog, and counting it fakes a ~270s median. We seed on that first poll and
    then time only prints that appear AFTERWARDS. Read-only, places nothing."""
    print(colored(f"\n  📏 🌙 MOON DEV's LAGCHECK — how fresh is the liquidation API?", "cyan", attrs=['bold']))
    print(colored(f"  trigger needs prints inside {TRIGGER_LOOKBACK_SEC}s. Anything slower "
                  f"than that can NEVER fire the bot.\n", "white"))
    url = f"https://api.moondev.com/api/all_liquidations/{TRIGGER_TIMEFRAME}.json"

    def poll():
        r = safe_get(url, headers={'X-API-Key': MOONDEV_API_KEY}, timeout=20)
        return r.json().get('liquidations', []) if (r is not None and r.status_code == 200) else []

    seen = set()
    for x in poll():
        seen.add(f"{x.get('exchange')}|{x.get('timestamp')}|{x.get('value')}")
    print(colored(f"  warm-up: seeded {len(seen)} prints already in the window "
                  f"(these do NOT count). Measuring {minutes} min...\n", "white"))

    lags = []
    t_end = time.time() + minutes * 60
    while time.time() < t_end:
        time.sleep(5)
        now = time.time()
        for x in poll():
            ts = x.get('timestamp')
            if not ts:
                continue
            k = f"{x.get('exchange')}|{ts}|{x.get('value')}"
            if k in seen:
                continue
            seen.add(k)
            lags.append((now - int(ts) / 1000.0, float(x.get('value', 0) or 0),
                         x.get('exchange', '?'), str(x.get('symbol', '')).upper()))
        left = int(t_end - time.time())
        print(colored(f"\r  ⏱  {left:>3}s left — {len(lags)} new prints timed so far...   ",
                      "white"), end="", flush=True)

    n = len(lags)
    print(colored(f"\n\n  RESULT: {n} fresh prints measured", "cyan", attrs=['bold']))
    if not n:
        print(colored("  ⚠️  no new prints at all — market was dead quiet. Run it again "
                      "during action before trusting this.\n", "yellow"))
        return
    vals = sorted(l[0] for l in lags)
    ok = sum(1 for v in vals if v <= TRIGGER_LOOKBACK_SEC)
    p50 = vals[n // 2]
    verdict_col = "green" if ok / n >= 0.9 else ("yellow" if ok / n >= 0.6 else "red")
    print(colored(f"  overall lag   min={vals[0]:.0f}s  p50={p50:.0f}s  max={vals[-1]:.0f}s", "white"))
    print(colored(f"  inside the {TRIGGER_LOOKBACK_SEC}s trigger window: {ok}/{n} "
                  f"({100*ok/n:.0f}%)", verdict_col, attrs=['bold']))
    print(colored("\n  BY EXCHANGE:", "white", attrs=['bold']))
    for ex in sorted(set(l[2] for l in lags)):
        e = sorted(l[0] for l in lags if l[2] == ex)
        eok = sum(1 for v in e if v <= TRIGGER_LOOKBACK_SEC)
        c = "green" if eok / len(e) >= 0.9 else ("yellow" if eok / len(e) >= 0.6 else "red")
        print(colored(f"     {ex:<12} n={len(e):<4} p50={e[len(e)//2]:>5.0f}s  max={e[-1]:>5.0f}s   "
                      f"inside window: {eok}/{len(e)}", c))
    btc = sorted([l for l in lags if l[3].startswith(COIN)])
    print(colored(f"\n  {COIN} prints: {len(btc)}", "white", attrs=['bold']))
    for l in btc[:10]:
        c = "green" if l[0] <= TRIGGER_LOOKBACK_SEC else "red"
        print(colored(f"     lag={l[0]:>5.0f}s  ${l[1]:>12,.0f}  {l[2]}", c))
    if ok / n >= 0.9:
        print(colored(f"\n  ✅ API is keeping up — the trigger can fire on real cascades.\n", "green", attrs=['bold']))
    else:
        print(colored(f"\n  ❌ API is still behind. {n-ok}/{n} prints arrive too late to ever "
                      f"fire the bot.\n     Set LIQ_SOURCE = \"auto\" to use the exchange "
                      f"sockets instead.\n", "red", attrs=['bold']))


# ============================================================================
# 🌙 MOON DEV - SELFTEST (live read-only smoke check — places NOTHING)
# ============================================================================
def selftest():
    """🌙 Moon Dev - read every real feed once and print exactly what the bot
    would do RIGHT NOW. NO ORDER IS EVER PLACED."""
    print(colored(f"\n  {BOT_EMOJI} 🌙 MOON DEV's {BOT_NAME} — LIVE READ-ONLY SMOKE CHECK {BOT_EMOJI}", "cyan", attrs=['bold']))
    print(colored("  (no order will be placed — read path only)\n", "yellow"))
    if not MOONDEV_API_KEY:
        print(colored("  ❌ MOONDEV_API_KEY missing from .env", "red"))
        return
    if LIQ_SOURCE in ("ws", "auto"):
        print(colored("  🔌 opening exchange sockets, listening 12s for live prints...", "cyan"))
        start_ws_feed()
        time.sleep(12)
    window_ts = get_current_window_ts()
    time_left = MARKET_DURATION - (int(time.time()) - window_ts)
    print(colored(f"  window     : btc-updown-5m-{window_ts}  "
                  f"({datetime.fromtimestamp(window_ts, ET).strftime('%H:%M')} ET)  time_left {time_left}s", "white"))
    info = get_market_tokens(window_ts)
    if not info:
        print(colored("  ❌ market not found on Gamma yet", "red"))
        return
    print(colored(f"  slug       : {info['slug']}", "white"))

    spot = get_btc_spot()
    snap = fetch_positions_snapshot(spot)
    if snap is None:
        print(colored("  ❌ Moon Dev positions.json unreachable", "red"))
        return
    age_s = f"{snap['age_sec']:.0f}s" if snap["age_sec"] is not None else "?"
    print(colored(f"  spot       : ${spot:,.2f} (live Binance — distances recomputed off this)", "white"))
    ql, qs = qualifying(snap["longs"]), qualifying(snap["shorts"])
    print(colored(f"  positions  : {len(snap['longs'])} {COIN} longs / {len(snap['shorts'])} {COIN} shorts "
                  f"in feed | age {age_s}", "white"))
    print(colored(f"  qualifying : {len(ql)} longs / {len(qs)} shorts "
                  f"(≤{NEAR_LIQ_PCT}% from liq AND ≥${MIN_POSITION_USD:,})", "cyan"))
    for tag, lst in (("LONG ", ql[:3]), ("SHORT", qs[:3])):
        for p in lst:
            print(colored(f"     {tag} ${_val(p)/1e3:>8.0f}k  {_dist(p):>5.2f}% away  "
                          f"liq ${float(p.get('liq_price', 0)):>10,.0f}  lev {p.get('leverage')}x", "white"))

    direction, side_label, pos, why = arm(snap)
    print(colored(f"\n  1️⃣ ARM      : {why}", "magenta", attrs=['bold']))
    if direction is None:
        print(colored("  DECISION   : NOT ARMED — would sit this window out.\n", "yellow", attrs=['bold']))
        return

    prints, src = get_prints()
    if prints is None:
        print(colored("  ❌ liquidation feed unreachable", "red"))
        return
    now_ms = time.time() * 1000
    fresh = [e for e in prints if (now_ms - e["ts_ms"]) / 1000.0 <= TRIGGER_LOOKBACK_SEC]
    print(colored(f"\n  liq feed   : [{src}]  {len(prints)} {COIN} prints held, "
                  f"{len(fresh)} inside the last {TRIGGER_LOOKBACK_SEC}s", "white"))
    if LIQ_SOURCE == "api":
        fresh_pct = (100.0 * len(fresh) / len(prints)) if prints else 0.0
        c = "green" if fresh_pct >= 50 else "yellow"
        print(colored(f"     {fresh_pct:.0f}% of held prints are inside the {TRIGGER_LOOKBACK_SEC}s "
                      f"trigger window  (run --lagcheck to measure properly)", c))
    else:
        for ex in ("binance", "bybit", "okx"):
            up = WS_CONN[ex]
            print(colored(f"     {'●' if up else '○'} socket {ex:<9} {'connected' if up else 'down'}"
                          f"   {COIN} prints captured: {WS_COUNT[ex]}", "green" if up else "red"))
    for e in sorted(prints, key=lambda e: -e["value"])[:5]:
        age = (now_ms - e["ts_ms"]) / 1000.0
        hot = e["side"] == side_label and e["value"] >= TRIGGER_LIQ_USD and age <= TRIGGER_LOOKBACK_SEC
        print(colored(f"     {'🔥' if hot else '· '} ${e['value']:>10,.0f} {e['side']:<5} "
                      f"{e['exchange']:<9} {age:>5.0f}s ago", "magenta" if hot else "white"))

    trig, diag = scan_for_trigger(prints, side_label)
    if trig is None:
        print(colored(f"\n  2️⃣ TRIGGER  : none — {diag['why']}", "yellow", attrs=['bold']))
        print(colored(f"  DECISION   : ARMED {direction}, WAITING. No order.\n", "yellow", attrs=['bold']))
        return

    print(colored(f"\n  2️⃣ TRIGGER  : 💥 ${trig['value']:,.0f} {trig['side']} liq on {trig['exchange']} "
                  f"{trig['age_sec']:.0f}s ago", "magenta", attrs=['bold']))
    token_id = info["by_outcome"].get(direction)
    bid, ask = get_book(token_id)
    print(colored(f"  {direction} book  : bid {bid}  ask {ask}", "white"))
    if ask is None:
        print(colored("  ❌ no ask on the book", "yellow"))
        return
    if ask > MAX_ENTRY_PRICE:
        print(colored(f"  DECISION   : ask ${ask:.2f} > cap ${MAX_ENTRY_PRICE:.2f} — would stand down.\n", "yellow", attrs=['bold']))
        return
    price = min(round(ask + CROSS_BUFFER, 2), 0.99)
    shares = max(float(MIN_SHARES), math.floor((USD_SIZE / price) * 100) / 100)
    print(colored(f"\n  3️⃣ WOULD FIRE: taker BUY {direction} {shares:.2f} sh @ ${price:.2f} "
                  f"(crossing the ${ask:.2f} ask)", "green", attrs=['bold']))
    print(colored(f"  4️⃣ WOULD HOLD: no exit — to expiration", "white"))
    print(colored(f"\n  ✅ read path OK — NOTHING WAS PLACED. 🌙 Moon Dev out.\n", "green", attrs=['bold']))


# ============================================================================
# 🌙 MOON DEV - MAIN
# ============================================================================
def main():
    if not MOONDEV_API_KEY:
        print(colored("❌ Moon Dev - MOONDEV_API_KEY missing from .env — the liq data IS the bot. Exiting.", "red"))
        sys.exit(1)
    if not PAPER_MODE and (not os.getenv(PRIVATE_KEY_ENV_NAME) or not os.getenv(PUBLIC_KEY_ENV_NAME)):
        print(colored(f"❌ Missing {PRIVATE_KEY_ENV_NAME}/{PUBLIC_KEY_ENV_NAME} in .env", "red"))
        sys.exit(1)

    if LIQ_SOURCE in ("ws", "auto"):
        start_ws_feed()
        log_event("🔌 exchange sockets opening (binance/bybit/okx) — real-time prints", "cyan")

    st = fresh_state()
    current_window = None
    last_draw = 0.0
    last_spot = 0.0
    log_event(f"{BOT_EMOJI} {BOT_NAME} spinning up" + (" (PAPER)" if PAPER_MODE else " — LIVE, REAL MONEY"), "cyan")
    log_event(f"🎯 arm ≤{NEAR_LIQ_PCT}% & ≥${MIN_POSITION_USD/1e3:.0f}k, fire on a ${TRIGGER_LIQ_USD:,} liq", "cyan")

    while True:
        now = time.time()
        window_ts = get_current_window_ts()
        time_left = MARKET_DURATION - (int(now) - window_ts)

        # 🌙 daily stop-loss bookkeeping (ET day)
        today = datetime.now(ET).strftime("%Y-%m-%d")
        if S["day"] != today:
            S["day"] = today
            S["daily_pnl"] = 0.0
            S["halted"] = False
        if not S["halted"] and S["daily_pnl"] <= DAILY_STOP_LOSS:
            S["halted"] = True
            log_event(f"🛑 DAILY STOP {S['daily_pnl']:+.2f} <= {DAILY_STOP_LOSS} — no new entries today", "red")

        resolve_pending_trades()
        check_kill_switch()

        # 🌙 window rollover: clean up any unfilled remainder, log the window, reset
        if window_ts != current_window:
            if current_window is not None:
                if not PAPER_MODE and st["fired"] and st["token_id"] and not st["filled"]:
                    cancel_token_orders(st["token_id"])
                    held, avg = position_for_token(st["token_id"])
                    if held is not None and held > 0:  # 🌙 late fill caught at the buzzer
                        st["filled"] = True
                        st["shares"] = held
                        if avg:
                            st["entry_price"] = avg
                        S["fills"] += 1
                        record_trade(st, current_window, held)
                        log_event(f"⚡ buzzer fill caught: {st['direction']} x{held:.1f}", "green")
                    elif st["action"] == "FIRED":
                        st["action"] = "NO_FILL"
                finalize_window(st, current_window)
                S["windows"] += 1
                if st["direction"] is None:
                    S["no_arm"] += 1
                elif not st["fired"]:
                    S["no_trigger"] += 1
                st = fresh_state()
            current_window = window_ts
            log_event(f"🔄 new window {datetime.fromtimestamp(window_ts, ET).strftime('%H:%M')} ET", "magenta")

        # 🌙 discover market
        if st["tokens"] is None:
            info = get_market_tokens(window_ts)
            if info:
                st["tokens"] = info["by_outcome"]
                st["slug"] = info["slug"]

        # 🌙 spot (dashboard context)
        if now - last_spot >= 5.0:
            last_spot = now
            px = get_btc_spot()
            if px:
                st["spot"] = px

        update_arm(st, now)
        check_trigger_and_fire(st, window_ts, time_left, now)
        watch_fill(st, window_ts, time_left, now)

        if now - last_draw >= 0.5:
            last_draw = now
            draw(st, time_left)
        time.sleep(0.3)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    if "--lagcheck" in sys.argv:
        mins = 6
        for a in sys.argv:
            if a.startswith("--minutes="):
                mins = int(a.split("=")[1])
        lagcheck(mins)
        sys.exit(0)
    tag = "PAPER MODE" if PAPER_MODE else f"LIVE (REAL MONEY, ${USD_SIZE}/trade)"
    print(colored(f"{BOT_EMOJI} Moon Dev's {BOT_NAME} — {tag}...", "cyan", attrs=['bold']))
    print(colored(f"🎯 arm on a ≥${MIN_POSITION_USD:,} {COIN} position within {NEAR_LIQ_PCT}% of liq", "cyan"))
    print(colored(f"💥 fire when a ${TRIGGER_LIQ_USD:,}+ liquidation prints on that side — then hold to expiry", "cyan"))
    print(colored(f"📒 trades: {TRADES_FILE}\n📒 eval:   {EVAL_LOG}", "cyan"))
    time.sleep(0.4)
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print()
            print(colored(f"  {BOT_EMOJI} MOON DEV — {BOT_NAME} STOPPED", "yellow", attrs=['bold']))
            print(colored(f"  🎯 windows {S['windows']}  armed {S['armed_windows']}  "
                          f"fired {S['fires']}  fills {S['fills']}  day P&L ${S['daily_pnl']:+.2f}", "green", attrs=['bold']))
            print(colored(f"  💾 {TRADES_FILE}", "cyan"))
            print(colored("  🌙 Moon Dev out — the cascade always comes for somebody.", "magenta"))
            print()
            break
        except Exception as e:
            print(colored(f"  💥 crashed: {type(e).__name__}: {e} — restarting in 3s", "red", attrs=['bold']))
            _CLIENT_CACHE = None
            time.sleep(3)
