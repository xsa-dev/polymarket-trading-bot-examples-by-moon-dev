#!/opt/anaconda3/envs/tflow/bin/python
"""
================================================================================
🌙 MOON DEV's STREAK SNAPPER v1.0 (July 18th Fleet, Bot #1)
================================================================================
Fade stretched same-direction streaks at the OPEN of the next 5-min BTC window.

THESIS (mined from 1,648 live windows + 104,762 windows of 52wk real BTC data):
  After 4+ consecutive same-direction 5-min windows whose cumulative move
  exceeds 3x the 1-hour ATR, the next window REVERSES ~54.3% of the time.
  Buy the reversal side with a limit at <= 52c at window open, hold to
  resolution. Positive in all 5 backtest quarters (worst: 52.2%).

RULES (do not "improve" these back out):
  ❌ NO plain streak-counting, without the 3x ATR stretch filter edge = 50.7%
  ❌ NEVER pay above 52c, the whole edge is 54.3% vs ~51c entry
  ❌ NO chasing, cancel if unfilled after 60 seconds
  ❌ NO mid-window exits, the 54.3% is measured open-to-close
  ✅ Enter in the FIRST 20 SECONDS of the new window (near-mid liquidity lives
     there, late-window stink bids got cancelled 95% of the time in past logs)
  ✅ One position per window max
  ✅ Skip on any feed gap, Moon Dev NEVER trades on fake/missing data
  ✅ Winners get redeemed by the existing OG_redeem.py flow (run separately)

Account: AUG14 | CLOB V2 SDK (V1 post_order now throws PolyApiException)
Built by Moon Dev 🌙
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
import math
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from termcolor import colored

# ============================================================================
# 🌙 MOON DEV - PATH SETUP
# ============================================================================
BOT_DIR = os.path.dirname(os.path.abspath(__file__))                 # fable_july_18th/
PROJECT_ROOT = os.path.dirname(os.path.dirname(BOT_DIR))             # repo root
MOON_DEV_API_PATH = "/Users/md/Dropbox/dev/github/moon-dev-trading-bots"

load_dotenv(os.path.join(PROJECT_ROOT, '.env'))
sys.path.insert(0, MOON_DEV_API_PATH)
from api import MoonDevAPI

# ============================================================================
# 🌙 MOON DEV - KEY PARAMS (the whole edge lives in these numbers)
# ============================================================================
PAPER_MODE = False                  # 🌙 LIVE FIRE! Flip back to True for paper testing

STREAK_MIN = 4                      # Need >= 4 consecutive same-direction windows
STRETCH_MULT = 3.0                  # |cum 4-window move| must be > 3x the 1h ATR
ATR_WINDOWS = 12                    # ATR = rolling mean |5-min change|, 12 windows = 1h
CUM_WINDOWS = 4                     # Cumulative move = the streak's LAST 4 window moves
LIMIT_CAP = 0.52                    # HARD price cap, paying 55c+ burns the 54.3% edge
ENTRY_WINDOW_SEC = 20               # Only enter in the first 20s of the new window
CANCEL_AFTER_SEC = 60               # Unfilled after 60s → cancel, NO chasing
SIZE_USD = 10                       # Flat $10 per trade (validation phase, first 200)

KILL_SWITCH_WR = 0.50               # Pause entries if trailing win rate < 50% (~2 sigma)
KILL_SWITCH_WINDOW = 50             # ...measured over last 50 resolved trades
KILL_SWITCH_MIN_TRADES = 20         # ...but only once we have 20 resolved trades

MARKET_DURATION = 300               # btc-updown-5m markets = 300 seconds
MIN_SHARES = 5                      # Polymarket minimum order size
PRICE_TICK = 0.01                   # Polymarket price tick
HISTORY_WINDOWS = 16                # Look back 16 completed windows (streak + ATR room)
GAMMA_DIR_LOOKBACK = 8              # Ask gamma (the real oracle) for the last 8 outcomes

# === Files ===
DATA_DIR = os.path.join(BOT_DIR, "data")
LOG_FILE = os.path.join(DATA_DIR, "streak_snapper_log.csv")

ET = timezone(timedelta(hours=-5))

# ============================================================================
# 🌙 MOON DEV - ACCOUNT CONFIGURATION (AUG14, keeps OG free)
# ============================================================================
ACCOUNT_SUFFIX = "_AUG14"
PRIVATE_KEY_ENV_NAME = f"PRIVATE_KEY{ACCOUNT_SUFFIX}"
PUBLIC_KEY_ENV_NAME = f"PUBLIC_KEY{ACCOUNT_SUFFIX}"
SIGNATURE_TYPE = 2                  # Gnosis Safe

# ============================================================================
# 🌙 MOON DEV - MOON DEV API SETUP (BTC ticks for window moves + ATR)
# ============================================================================
api = MoonDevAPI()
if not api.api_key:
    print(colored("❌ Moon Dev - MOONDEV_API_KEY not found in .env!", "red"))
    sys.exit(1)
print(colored("✅ Moon Dev - Moon Dev API loaded!", "green"))

# ============================================================================
# 🌙 MOON DEV - V2 CLOB CLIENT (V1 post_order is dead)
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


def place_limit_buy(token_id, price, size):
    """🌙 Moon Dev - GTC limit BUY at <= 52c (post_only=False so it can cross
    a cheap ask; FAK/FOK 400 when the crossable amount is < $1, verified live).
    If the book is above our price the order RESTS at our limit, exactly what
    Streak Snapper wants at window open. Cancelled after 60s if unfilled."""
    from py_clob_client_v2.clob_types import OrderArgs, OrderType
    client = _build_client()
    order_args = OrderArgs(token_id=str(token_id), price=float(price), size=float(size), side="BUY")
    print(colored(f"   🎯 Moon Dev - Limit BUY @ ${price:.2f} x{size} shares (GTC, 60s fuse)", "cyan", attrs=['bold']))
    try:
        resp = client.create_and_post_order(order_args, order_type=OrderType.GTC, post_only=False)
        return resp or {}
    except Exception as e:
        err = str(e).lower()
        if any(t in err for t in ['timeout', 'readtimeout', 'duplicated', 'request exception']):
            print(colored("   ⚠️ Moon Dev - order timed out, will verify via positions", "yellow"))
            return {"status": "timeout"}
        print(colored(f"   ❌ Moon Dev - order failed: {type(e).__name__}: {e}", "red"))
        return {}


def cancel_token_orders(token_id):
    """🌙 Moon Dev - Cancel resting GTC orders on a token (the 60s no-chase fuse)."""
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
# 🌙 MOON DEV - MARKET DISCOVERY (btc-updown-5m-{T})
# ============================================================================

def get_current_market_timestamp():
    return (int(time.time()) // MARKET_DURATION) * MARKET_DURATION


def get_market_info(market_ts):
    """🌙 Moon Dev - Find the active btc-updown-5m market + UP/DOWN token ids"""
    market_slug = f"btc-updown-5m-{market_ts}"
    r = requests.get("https://gamma-api.polymarket.com/markets",
                     params={'slug': market_slug, 'closed': 'false', 'active': 'true'},
                     timeout=10)
    if r.status_code != 200:
        return None
    markets = r.json()
    if not markets:
        return None
    market = markets[0]
    token_ids = json.loads(market['clobTokenIds'])   # [UP, DOWN] matches outcomes order
    print(colored(f"   ✅ Moon Dev - Found market: {market['question']}", "green"))
    return {
        'market_id': market['id'],
        'up_token_id': token_ids[0],
        'down_token_id': token_ids[1],
        'question': market['question'],
        'slug': market_slug,
    }


def get_order_book(token_id):
    """🌙 Moon Dev - Best bid/ask from CLOB"""
    r = requests.get("https://clob.polymarket.com/book", params={'token_id': token_id}, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    bids, asks = data.get('bids', []), data.get('asks', [])
    if not bids or not asks:
        return None
    best_bid = float(bids[-1]['price'])
    best_ask = float(asks[0]['price'])
    return {'best_bid': best_bid, 'best_ask': best_ask, 'spread': best_ask - best_bid}

# ============================================================================
# 🌙 MOON DEV - WINDOW HISTORY (real oracle outcomes + real BTC tick moves)
# ============================================================================

def gamma_window_direction(market_ts):
    """🌙 Moon Dev - REAL resolved direction of a past window straight from the
    oracle (gamma outcomePrices). Returns 'UP', 'DOWN', or None if unresolved."""
    slug = f"btc-updown-5m-{market_ts}"
    r = requests.get("https://gamma-api.polymarket.com/markets",
                     params={'slug': slug}, timeout=10)
    if r.status_code != 200 or not r.json():
        return None
    prices = json.loads(r.json()[0].get('outcomePrices', '[]') or '[]')
    if len(prices) != 2:
        return None
    up_price = float(prices[0])
    if up_price == 1.0:
        return "UP"
    if up_price == 0.0:
        return "DOWN"
    return None  # not resolved yet


def get_window_history(current_ts):
    """🌙 Moon Dev - Last HISTORY_WINDOWS completed 5-min windows, oldest→newest.
    USD open/close/move from REAL Moon Dev API BTC ticks (ATR + stretch math).
    Direction from gamma (the actual oracle) for the recent windows, tick
    direction as fallback when gamma hasn't resolved yet.
    Returns list of dicts or None on a feed gap, NO fake data, EVER."""
    tick_response = api.get_ticks("BTC", "4h", limit=50000)
    if not tick_response or not isinstance(tick_response, dict):
        print(colored("   ❌ Moon Dev - BTC tick feed is down, skipping, never faking data!", "red"))
        return None
    all_ticks = tick_response.get('ticks', [])
    if len(all_ticks) < 100:
        print(colored(f"   ❌ Moon Dev - Only {len(all_ticks)} ticks, feed gap, skipping!", "red"))
        return None

    windows = []
    for i in range(HISTORY_WINDOWS, 0, -1):
        w_start = current_ts - i * MARKET_DURATION
        w_end = w_start + MARKET_DURATION
        w_ticks = [t for t in all_ticks if w_start * 1000 <= t.get('t', 0) < w_end * 1000]
        if len(w_ticks) < 2:
            print(colored(f"   ❌ Moon Dev - Feed gap in window {w_start} ({len(w_ticks)} ticks), skipping signal!", "red"))
            return None
        w_ticks.sort(key=lambda t: t.get('t', 0))
        w_open = float(w_ticks[0].get('p', w_ticks[0].get('price', 0)))
        w_close = float(w_ticks[-1].get('p', w_ticks[-1].get('price', 0)))
        move = w_close - w_open
        windows.append({'ts': w_start, 'open': w_open, 'close': w_close,
                        'move_usd': move, 'dir': "UP" if move >= 0 else "DOWN"})

    # 🌙 Moon Dev - Overwrite tick direction with the REAL oracle where resolved
    for w in windows[-GAMMA_DIR_LOOKBACK:]:
        oracle_dir = gamma_window_direction(w['ts'])
        if oracle_dir:
            w['dir'] = oracle_dir
    return windows


def compute_signal(windows):
    """🌙 Moon Dev - The whole strategy in one function:
      streak_len  = consecutive same-direction windows ending at the newest
      atr_usd     = mean |5-min move| over the last 12 windows (1 hour)
      cum_move    = sum of the streak's last 4 window moves (signed, USD)
      SIGNAL when streak_len >= 4 AND |cum_move| > 3x ATR.
    Returns (streak_len, streak_dir, cum_move_usd, atr_usd, stretch_ratio, signal)."""
    streak_dir = windows[-1]['dir']
    streak_len = 0
    for w in reversed(windows):
        if w['dir'] == streak_dir:
            streak_len += 1
        else:
            break

    atr_usd = sum(abs(w['move_usd']) for w in windows[-ATR_WINDOWS:]) / ATR_WINDOWS
    cum_move_usd = sum(w['move_usd'] for w in windows[-CUM_WINDOWS:])
    stretch_ratio = abs(cum_move_usd) / atr_usd if atr_usd > 0 else 0.0
    signal = streak_len >= STREAK_MIN and abs(cum_move_usd) > STRETCH_MULT * atr_usd
    return streak_len, streak_dir, cum_move_usd, atr_usd, stretch_ratio, signal

# ============================================================================
# 🌙 MOON DEV - LOGGING (spec columns, EVERY window: entries AND skips)
# ============================================================================
LOG_COLS = ['snapshot_time', 'window_slug', 'streak_len', 'streak_dir', 'cum_move_usd',
            'atr_usd', 'stretch_ratio', 'fade_side', 'limit_price', 'action',
            'entry_price', 'shares', 'outcome', 'won', 'pnl_usd']


def log_row(window_slug, streak_len, streak_dir, cum_move_usd, atr_usd, stretch_ratio,
            fade_side, limit_price, action, entry_price='', shares='', outcome=''):
    """🌙 Moon Dev - Append one row (the NO_FILL/SKIP rows are gold for auditing)"""
    os.makedirs(DATA_DIR, exist_ok=True)
    row = pd.DataFrame([{
        'snapshot_time': datetime.now().isoformat(),
        'window_slug': window_slug,
        'streak_len': streak_len if streak_len is not None else '',
        'streak_dir': streak_dir or '',
        'cum_move_usd': round(cum_move_usd, 2) if cum_move_usd is not None else '',
        'atr_usd': round(atr_usd, 2) if atr_usd is not None else '',
        'stretch_ratio': round(stretch_ratio, 2) if stretch_ratio is not None else '',
        'fade_side': fade_side or '',
        'limit_price': round(limit_price, 2) if limit_price is not None else '',
        'action': action,
        'entry_price': entry_price,
        'shares': shares,
        'outcome': outcome,
        'won': '',
        'pnl_usd': '',
    }], columns=LOG_COLS)
    header = not os.path.exists(LOG_FILE)
    row.to_csv(LOG_FILE, mode='a', header=header, index=False)
    color = "green" if action == "ENTER" else "yellow"
    print(colored(f"   📝 Moon Dev - Logged: {action} ({window_slug})", color))

# ============================================================================
# 🌙 MOON DEV - RESOLUTION + KILL SWITCH (gamma outcomePrices = the oracle)
# ============================================================================

def resolve_pending_trades():
    """🌙 Moon Dev - Mark PENDING entries WIN/LOSS via gamma outcomePrices once
    the market resolves. Feeds the trailing-50 kill switch."""
    if not os.path.exists(LOG_FILE):
        return
    df = pd.read_csv(LOG_FILE)
    pending = df[(df['action'] == 'ENTER') & (df['outcome'] == 'PENDING')]
    if pending.empty:
        return
    changed = False
    for idx, row in pending.iterrows():
        market_ts = int(str(row['window_slug']).rsplit('-', 1)[-1])
        # only check markets that ended > 30s ago (give the oracle a beat)
        if time.time() < market_ts + MARKET_DURATION + 30:
            continue
        winner = gamma_window_direction(market_ts)
        if winner is None:
            continue
        won = row['fade_side'] == winner
        shares = float(row['shares'])
        cost = float(row['entry_price']) * shares
        pnl = (shares - cost) if won else -cost
        df.at[idx, 'outcome'] = 'WIN' if won else 'LOSS'
        df.at[idx, 'won'] = won
        df.at[idx, 'pnl_usd'] = round(pnl, 2)
        changed = True
        if won:
            print(colored(f"   🚀 Moon Dev caught the snap-back! +${pnl:.2f} ({row['window_slug']})", "green"))
        else:
            print(colored(f"   😤 Streak kept running on Moon Dev... -${cost:.2f} ({row['window_slug']})", "red"))
    if changed:
        df.to_csv(LOG_FILE, index=False)


def kill_switch_active():
    """🌙 Moon Dev - True = PAUSE. Trailing-50 resolved win rate < 50%
    (~2 sigma below the 54.3% backtest, stop and review per the spec)."""
    if not os.path.exists(LOG_FILE):
        return False
    df = pd.read_csv(LOG_FILE)
    resolved = df[df['outcome'].isin(['WIN', 'LOSS'])].tail(KILL_SWITCH_WINDOW)
    if len(resolved) < KILL_SWITCH_MIN_TRADES:
        return False
    wr = (resolved['outcome'] == 'WIN').mean()
    if wr < KILL_SWITCH_WR:
        print(colored(f"   🚨 Moon Dev - KILL SWITCH! Trailing-{len(resolved)} win rate "
                      f"{wr*100:.1f}% < {KILL_SWITCH_WR*100:.0f}%, entries PAUSED", "red", attrs=['bold']))
        return True
    return False


def print_trade_tracker():
    """🌙 Moon Dev - Clickable tracker of past fills, printed FIRST every cycle."""
    if not os.path.exists(LOG_FILE):
        return
    df = pd.read_csv(LOG_FILE)
    trades = df[df['action'] == 'ENTER']
    if trades.empty:
        return
    print(colored(f"============ 🌙 MOON DEV'S TRADE TRACKER ({len(trades)} trades) 🌙 ============", "cyan", attrs=['bold']))
    for _, t in trades.tail(20).iterrows():
        ts = pd.to_datetime(t['snapshot_time']).strftime('%m-%d %H:%M')
        if t['outcome'] == 'WIN':
            status, color = f"WIN  +${float(t['pnl_usd']):.2f}", "green"
        elif t['outcome'] == 'LOSS':
            status, color = f"LOSS -${abs(float(t['pnl_usd'])):.2f}", "red"
        else:
            status, color = "PENDING", "white"
        print(colored(f"{ts} | SNAP {t['fade_side']:<4} @ {float(t['entry_price']):.2f} | {status:<12} | "
                      f"https://polymarket.com/event/{t['window_slug']}", color))
    print(colored("=" * 67, "cyan", attrs=['bold']))


def print_session_summary():
    """🌙 Moon Dev - Quick PnL + win rate readout"""
    if not os.path.exists(LOG_FILE):
        print(colored("   📋 Moon Dev - No history yet (first run)", "yellow"))
        return
    df = pd.read_csv(LOG_FILE)
    entries = df[df['action'] == 'ENTER']
    resolved = entries[entries['outcome'].isin(['WIN', 'LOSS'])]
    wins = (resolved['outcome'] == 'WIN').sum()
    pnl = pd.to_numeric(resolved['pnl_usd'], errors='coerce').sum()
    print(colored(f"\n📋 Moon Dev - STREAK SNAPPER HISTORY", "cyan", attrs=['bold']))
    print(colored(f"   Fills: {len(entries)} | Resolved: {len(resolved)} | Wins: {wins} | "
                  f"WR: {(wins/len(resolved)*100) if len(resolved) else 0:.1f}% | "
                  f"PnL: ${pnl:+.2f}", "white"))

# ============================================================================
# 🌙 MOON DEV - MAIN BOT
# ============================================================================

class StreakSnapperBot:

    def run_window(self, market_ts):
        """🌙 Moon Dev - One 5-min window: signal at open, 60s fuse, hold to resolution."""
        market_et = datetime.fromtimestamp(market_ts, tz=ET)
        slug = f"btc-updown-5m-{market_ts}"
        print("")
        print_trade_tracker()  # 🌙 Moon Dev - tracker FIRST, clickable links up top
        print(colored(f"\n{'='*70}", "cyan"))
        print(colored(f"🌙 Moon Dev - STREAK SNAPPER WINDOW OPEN | {market_et.strftime('%I:%M:%S%p ET')} | {slug}", "cyan", attrs=['bold']))
        print(colored(f"{'='*70}", "cyan"))

        # 🌙 Moon Dev - Housekeeping first: resolve old trades, check kill switch
        resolve_pending_trades()
        paused = kill_switch_active()

        # === SIGNAL: last 16 windows of REAL data ===
        windows = get_window_history(market_ts)
        if windows is None:
            log_row(slug, None, None, None, None, None, None, None, "SKIP_FEED_GAP")
            return

        streak_len, streak_dir, cum_move, atr, stretch, signal = compute_signal(windows)
        recent = " ".join("🟢" if w['dir'] == "UP" else "🔴" for w in windows[-8:])
        print(colored(f"   📊 Last 8 windows: {recent}", "cyan"))
        print(colored(f"   📊 Streak: {streak_len}x {streak_dir} | Cum move (last {CUM_WINDOWS}): "
                      f"${cum_move:+,.0f} | 1h ATR: ${atr:,.0f} | Stretch: {stretch:.1f}x", "cyan"))

        if not signal:
            reason = "SKIP_NO_STREAK" if streak_len < STREAK_MIN else "SKIP_STRETCH"
            log_row(slug, streak_len, streak_dir, cum_move, atr, stretch, None, None, reason)
            print(colored(f"   😴 Moon Dev - No signal ({reason}). Watching...", "white"))
            return

        fade_side = "DOWN" if streak_dir == "UP" else "UP"
        print(colored(f"🌙 Moon Dev's Streak Snapper: {streak_len} straight {streak_dir}s, "
                      f"stretched {stretch:.1f}x ATR, snapping back with {fade_side} @ {int(LIMIT_CAP*100)}c!",
                      "magenta", attrs=['bold']))

        if paused:
            log_row(slug, streak_len, streak_dir, cum_move, atr, stretch, fade_side, None, "KILL_SWITCH")
            return

        # === Must fire within the first 20 seconds of the window ===
        market_info = None
        while time.time() - market_ts <= ENTRY_WINDOW_SEC:
            market_info = get_market_info(market_ts)
            if market_info:
                break
            print(colored("   🔄 Moon Dev - Market not indexed yet, retrying...", "yellow"))
            time.sleep(2)
        if not market_info:
            log_row(slug, streak_len, streak_dir, cum_move, atr, stretch, fade_side, None, "SKIP_NO_MARKET")
            print(colored("   ❌ Moon Dev - Couldn't find the market inside 20s, no chasing!", "red"))
            return
        if time.time() - market_ts > ENTRY_WINDOW_SEC:
            log_row(slug, streak_len, streak_dir, cum_move, atr, stretch, fade_side, None, "SKIP_LATE")
            print(colored("   ⏰ Moon Dev - Missed the 20s open window, no chasing!", "yellow"))
            return

        token_id = market_info['up_token_id'] if fade_side == "UP" else market_info['down_token_id']
        book = get_order_book(token_id)
        ask = book['best_ask'] if book else None
        # 🌙 Moon Dev - limit at the cap, or at the ask if it's already cheaper
        limit_price = LIMIT_CAP if ask is None else min(LIMIT_CAP, ask)
        limit_price = round(math.floor(limit_price / PRICE_TICK) * PRICE_TICK, 2)
        shares = max(MIN_SHARES, math.floor(SIZE_USD / limit_price))

        print(colored(f"\n   ⚡⚡⚡ MOON DEV SNAP: BUY {fade_side} @ ${limit_price:.2f} x{shares} "
                      f"(${shares*limit_price:.2f}) | ask={ask} ⚡⚡⚡",
                      "green" if fade_side == "UP" else "red", attrs=['bold']))
        print(colored(f"   🔗 https://polymarket.com/event/{slug}", "cyan"))

        # === Place + 60s fuse ===
        if PAPER_MODE:
            filled = ask is not None and ask <= limit_price
            print(colored(f"   📄 Moon Dev - PAPER MODE: {'simulated fill' if filled else 'NO FILL (ask above cap)'}", "yellow", attrs=['bold']))
        else:
            resp = place_limit_buy(token_id, limit_price, shares)
            if not resp:
                log_row(slug, streak_len, streak_dir, cum_move, atr, stretch, fade_side, limit_price, "ORDER_FAILED")
                return
            placed_at = time.time()
            filled = False
            while time.time() - placed_at < CANCEL_AFTER_SEC:
                time.sleep(5)
                if get_position_size(token_id) > 0:
                    filled = True
                    break
            if not filled:
                print(colored(f"   ✂️ Moon Dev - No fill in {CANCEL_AFTER_SEC}s, cancelling. NO chasing!", "yellow"))
                cancel_token_orders(token_id)
                time.sleep(3)
                filled = get_position_size(token_id) > 0  # late partial fill check

        if not filled:
            log_row(slug, streak_len, streak_dir, cum_move, atr, stretch, fade_side, limit_price, "NO_FILL")
            return

        held = shares if PAPER_MODE else get_position_size(token_id)
        log_row(slug, streak_len, streak_dir, cum_move, atr, stretch, fade_side, limit_price,
                "ENTER", entry_price=limit_price, shares=held, outcome='PENDING')
        print(colored(f"   🎲 Moon Dev - FILLED {held} {fade_side}, holding to resolution "
                      f"(no mid-window exits, the 54.3% is open-to-close)", "white", attrs=['bold']))

# ============================================================================
# 🌙 MOON DEV - MAIN ENTRY
# ============================================================================

def main():
    print(colored("""
╔══════════════════════════════════════════════════════════════════════════════╗
║   🌙 MOON DEV's STREAK SNAPPER v1.0 🌙                                      ║
║   4+ same-direction windows + >3x 1h ATR stretch → fade at window OPEN     ║
║   Limit <= 52c | 60s fuse, no chasing | Hold to resolution | 54.3% edge    ║
╚══════════════════════════════════════════════════════════════════════════════╝
    """, "cyan", attrs=['bold']))

    print(colored("⚙️  Moon Dev - Configuration:", "yellow", attrs=['bold']))
    print(colored(f"   📄 PAPER MODE:     {PAPER_MODE} {'(flip to False for live!)' if PAPER_MODE else '🔴 LIVE FIRE'}", "yellow" if PAPER_MODE else "red"))
    print(colored(f"   🔁 Signal:         streak >= {STREAK_MIN} AND |cum {CUM_WINDOWS}-window move| > {STRETCH_MULT}x 1h ATR ({ATR_WINDOWS} windows)", "white"))
    print(colored(f"   💰 Entry:          limit BUY reversal side <= {LIMIT_CAP:.2f}, first {ENTRY_WINDOW_SEC}s of window", "white"))
    print(colored(f"   ✂️  No-chase fuse:  cancel if unfilled after {CANCEL_AFTER_SEC}s", "white"))
    print(colored(f"   💵 Size:           ${SIZE_USD} flat (validation phase, first 200 trades)", "white"))
    print(colored(f"   🚨 Kill switch:    trailing-{KILL_SWITCH_WINDOW} WR < {KILL_SWITCH_WR*100:.0f}% → pause", "white"))
    print(colored(f"   🎯 One position per window | Hold to resolution | Redeem via OG_redeem.py", "white"))

    print_session_summary()
    print("")
    print_trade_tracker()  # 🌙 Moon Dev - seed the tracker display from the CSV

    # 🌙 Moon Dev - Smoke-test the tick feed before going live (NEVER run blind)
    print(colored("\n📡 Moon Dev - Testing BTC tick feed...", "yellow", attrs=['bold']))
    windows = get_window_history(get_current_market_timestamp())
    if windows is None:
        print(colored("❌ Moon Dev - Window history feed is dead, refusing to run on no data!", "red"))
        sys.exit(1)
    streak_len, streak_dir, cum_move, atr, stretch, signal = compute_signal(windows)
    print(colored(f"   ✅ {len(windows)} windows loaded | streak {streak_len}x {streak_dir} | "
                  f"1h ATR ${atr:,.0f} | stretch {stretch:.1f}x | signal={signal}", "cyan"))

    print(colored(f"\n{'='*70}", "green"))
    print(colored("🚀 Moon Dev - STREAK SNAPPER LIVE! Waiting for stretched streaks to snap...", "green", attrs=['bold']))
    print(colored("   Press Ctrl+C to stop", "yellow"))
    print(colored(f"{'='*70}\n", "green"))

    bot = StreakSnapperBot()
    last_processed_ts = None
    while True:
        try:
            market_ts = get_current_market_timestamp()
            elapsed = int(time.time()) - market_ts

            # 🌙 Moon Dev - Only fire at a FRESH window open (first 20s, one shot per window)
            if market_ts != last_processed_ts and elapsed <= ENTRY_WINDOW_SEC:
                last_processed_ts = market_ts
                bot.run_window(market_ts)
            elif market_ts != last_processed_ts:
                # started mid-window, mark it seen, wait for the next open
                last_processed_ts = market_ts
                print(colored(f"   ⏰ Moon Dev - {elapsed}s into window {market_ts}, waiting for the next open...", "yellow"))

            next_ts = market_ts + MARKET_DURATION
            wait = next_ts - time.time()
            if wait > 0:
                mins, secs = divmod(int(wait), 60)
                print(colored(f"   💤 Moon Dev - Next window in {mins}:{secs:02d}...", "white"))
                time.sleep(wait + 0.5)

        except KeyboardInterrupt:
            print(colored("\n\n🛑 Moon Dev - Streak Snapper stopped! Clean shutdown ✅", "yellow", attrs=['bold']))
            resolve_pending_trades()
            print_session_summary()
            break


if __name__ == "__main__":
    print("🌙 Moon Dev's Streak Snapper - stretched streaks snap back, let's go! 🚀")
    main()
