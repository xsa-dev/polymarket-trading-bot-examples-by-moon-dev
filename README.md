# 🌙 Polymarket Trading Bot Examples, by Moon Dev

Open-source Polymarket trading bot infrastructure, built **live on YouTube** by Moon Dev.

## ⚠️ Read this before you touch anything

**There is nothing plug-and-play in this repo.** Let me say that louder for the people
in the back: **NOBODY is ever going to hand you a plug-and-play bot that makes $1 million
overnight.** Not me, not anyone. If someone tells you otherwise, they are selling you
something.

What this repo IS: real infrastructure and real bots that Moon Dev has been building
live on stream, the wins, the losses, the breakeven grinders, and the autopsies of the
bots that didn't work. Since I'm doing all this hard work live on YouTube anyway, I might
as well share it to save you some time. That's the whole deal.

* 🚫 **This is NOT financial advice.** None of it. Ever.
* 🚫 **These bots are NOT profitable money printers.** Several of them exist precisely
  because a previous version LOST money and taught us something.
* ⚠️ **Use any of this in your own system 100% at your own risk.** Trading is risky.
  Prediction markets are risky. Automated trading is riskier. You can and will lose money.
* 🔧 Expect to do real work: get your own API keys, wire up your own accounts, read the
  code, understand every line before you run it with a single dollar.

Got questions? Join the Zoom call: **https://moondev.com/t/polymarket-github**

## What's in here

```
├── .env_example        ← the environment variables you'd need (copy to .env, fill in YOUR keys)
├── 5_minute_bots/      ← bots for Polymarket's BTC 5-minute Up/Down markets
│   ├── liq_cascade_chaser/   ← one folder per strategy:
│   ├── streak_snapper/          README (what it does + the data behind it)
│   ├── box_builder/             + the bot code
│   ├── corridor_collector/
│   └── flip_harvester_ml/    ← the river online-learning one (needs `river` + `polars`)
```

Every bot folder contains:
* **README.md**, what the strategy does, in plain english, why it exists, and the real
  numbers behind it (zero synthetic data, that's a hard rule around here)
* **the bot code**, heavily commented, logs every window (entries AND skips) to CSV

## Setup (the honest version)

1. Copy `.env_example` to `.env` and fill in **your own** keys. The `.env` file is
   gitignored, never, ever commit secrets. Ever.
2. These bots use the **V2 Polymarket CLOB SDK** (`py_clob_client_v2`), the V1 client's
   order placement is dead (throws `PolyApiException`).
3. Some bots need the Moon Dev API for liquidation/tick feeds (`MOONDEV_API_KEY`), you'll
   need your own data source or key there.
4. Paths, python env (`tflow` conda env), and account naming (`_AUG14` suffix) are from
   Moon Dev's machine. **You will need to adapt them.** See "nothing plug-and-play," above.
5. Every bot has a `PAPER_MODE` flag at the top. If you insist on running one, start there.

## The philosophy

I build in iteration loops: research agents mine the real trade logs → each proposes its
single best idea → the ideas get built with full logging → they run small and live → the
logs crown a winner (or, more often, teach us why the idea was wrong) → repeat. The logging
IS the product. A bot that loses money but logs every decision is worth more than a bot
that "wins" and can't tell you why.

If that iteration grind sounds less sexy than "run this bot, get rich", good. That's the
point. That's what real algo trading looks like.

Questions, ideas, want to watch this get built? Zoom call link:
**https://moondev.com/t/polymarket-github**

***
*Built live on YouTube by Moon Dev 🌙, not financial advice, no warranties, use at your
own risk. Every market decision you make is yours alone.*
