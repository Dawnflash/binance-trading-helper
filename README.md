# Binance trading helper

This python script creates a market buy order followed by limit and/or market sell orders with a set profit. This only works at binance.com.

Do not overuse, Binance only allows 100 orders per 10s and roughly 1200 requests per minute. You might get rate limited otherwise.

## Requirements

* Python 3.6 or higher
* Requests module (install: `pip install requests`)

## Instructions

0. Create a binance.com API key (allow basic info and spot trading)
1. Copy `.env.example` to `.env` and fill in your Binance API key+secret
2. Override settings in `.env` if you wish, settings `DEFAULT_*` may be overridden at runtime
3. Run `main.py` using Python 3 and respond to prompts

## Configuration

The script has configurable defaults in `.env` (make sure to copy `.env.example` to `.env` first).
Fields starting with `DEFAULT_` may be overridden at runtime unless you set `DEFAULT_OVERRIDE=1`, which skips all prompts and lets you restart the script very quickly.

I will explain the configuration options here.

* `BINANCE_API_KEY` and `BINANCE_API_SECRET` must contain your Binance API credentials. Get yours on your Binance account page, make sure to allow `Can Read` and `Enable Spot & Margin Trading` (turned on by default). No need to enable withdrawals or Futures!
* `BASE_API_URL` contains the Binance API base URL to use. Do not change this unless necessary.
* `SERVER_HOST` and `SERVER_PORT` specify the host and port for the coin name listener. Use this to receive a coin name via HTTP (from hooks like the one [here](https://github.com/tobyyy/tg-bps-script)).
* `DEFAULT_OVERRIDE` may be set to `1` to enable prompts or `0` to disable them. Set it to `1` if you desire to make changes on startup.
* `DEFAULT_QCOIN` is the name of your quote asset (coin): the coin you wish to sell and later buy back with profit
* `DEFAULT_BUY_PERC` is the percentage of your quote asset balance you wish to sell. If prompts are not disabled, you can change the exact quote asset amount on startup
* `DEFAULT_SELL_PERC` is the percentage of bought base asset (the coin to buy with the quote asset) you wish to sell at once. Keep this at 100 if you wish to sell everything this script buys at once. Otherwise, it will sell the coin until the remaining amount falls under this percentage. Then it will sell the rest at once.
* `DEFAULT_PROFIT` is your desired profit. Mind that setting your profit very high may impair your ability to set a successful limit sell. You should use `HYBRID` or `MARKET` strategies with high profits (such as >100%)
* `DEFAULT_SELL_STRATEGY` is the sell strategy you wish to use. See [Sell strategies](#sell-strategies) for details. The options are `LIMIT`, `MARKET` and `HYBRID`.
* `DEFAULT_MIN_PROFIT` (used with `HYBRID` and `LIMIT` sell strategies) is the minimum profit (in %) you are willing to accept using a limit sell. If the maximum price Binance allows falls between your `MIN_PROFIT` and `PROFIT`, a limit sell will be created at the maximum allowed limit.
* `DEFAULT_ALLOW_LIMIT_INCREASE` (set to `0` to disable or `1` to enable, used with `HYBRID` and `LIMIT` sell strategies) to determine if your target profit should be increased for a limit sell attempt to match the current maximum price limit. For example, if you set `PROFIT` to 40% and Binance allows a 50% profit maximum, your target profit will increase to 50%. If `PROFIT` exceeds this limit, this has no effect.

## Sell strategies

The script supports the following strategies to sell your coin with profit (default strategy is chosen with `DEFAULT_SELL_STRATEGY`):

* `LIMIT` only allows limit sell orders to be made. You are limited by Binance's upper profit limit which the script calculates before attempting to sell. A limit sell will be made if your `MIN_PROFIT` is lower or equal to the current limit. Market sells won't be attempted. Using this strategy with very high profits might fail to create a successful order.
* `MARKET` only allows market sell orders to be made. You are **not** limited by Binance's profit limits but there is no guarantee that you get the profit you desire. The script makes a market sell if the last traded price exceeds your target profit. Limit sell orders won't be attempted.
* `HYBRID` allows both market and limit orders to be made. First, a market order is attempted if the last traded price is above your target profit, then a limit sell order is attempted if the upper limit matches your profit criteria (`MIN_PROFIT` and `PROFIT`).

## How it works

The script first goes through the initial configuration which uses defaults from `.env` and if `DEFAULT_OVERRIDE=1` prompts are displayed to let you override defaults. The script then displays your settings, starts an HTTP server listening for coin name hooks, and displays a prompt to enter a base coin name manually.

Once you enter the base coin (manually or via the HTTP hook) and the trading pair selected is available for trading, the script will forbid further coin names from being entered and immediately buy your base asset with your quote asset using a market buy.

Once the base asset is bought, the script will start attempts to sell it using the provided sell strategy. It will attempt to sell your base asset until either

* all purchased base coins are sold (or put on the order with limit sells)
* `Ctrl+C` is pressed (`KeyboardInterrupt`)

The script will make at most 91 orders total (including the market buy). If 90 orders have already been made, the remaining purchased base asset amount will be sold at once if possible.

If at any given time for a given strategy no sell order may be made, the script will collect fresh API data and try again until it sells everything or you interrupt it with `Ctrl+C`.
