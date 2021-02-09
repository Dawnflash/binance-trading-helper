# Binance trading helper

This python script creates a market buy order followed by a limit/OCO or market sell order with a set profit. This only works on binance.com.

You tell the script how much of your quote currency to sell and it buys the base currency with it. The following sell orders honor your profit/loss choices.

## Requirements

* Python 3.6 or higher
* AIOHTTP module (install: `pip install aiohttp`)
* Websockets module (install: `pip install websockets`)

## Instructions

0. Create a binance.com API key (allow basic info and spot trading)
1. Copy `.env.example` to `.env` and fill in your Binance API key+secret
2. Override settings in `.env` if you wish, settings `DEFAULT_*` may be overridden at runtime
3. Run `main.py` using Python 3 and respond to prompts

## Configuration

The script stores configuration in `.env` (make sure to copy `.env.example` to `.env` first).
Overridable defaults are fields beginning with `DEFAULT_`. They may be overridden at runtime unless you set `PROMPT_OVERRIDE=1`, which skips all prompts and lets you restart the script very quickly. Furthermore, you may override the defaults here by passing them via environmental variables. Use this when automating the script.

I will explain the configuration options here.

* `BINANCE_API_KEY` and `BINANCE_API_SECRET` must contain your Binance API credentials. Get yours on your Binance account page, make sure to allow `Can Read` and `Enable Spot & Margin Trading` (turned on by default). No need to enable withdrawals or Futures!
* `BASE_API_URL` contains the Binance API base URL to use. Do not change this unless necessary.
* `BASE_WSAPI_URL` contains the Binance WebSockets API base URL to use. Do not change this unless necessary.
* `SERVER_HOST` and `SERVER_PORT` specify the host and port for the coin name listener. Use this to receive a coin name via HTTP (from hooks like the one [here](https://github.com/tobyyy/tg-bps-script)).
* `BAILOUT` lets you sell all remaining coins immediately when pressing `Ctrl+C` (see [Bailout](#bailout)). Set to 0 if you don't like this feature. If used with the `LIMIT` sell type, after placing your limit/OCO order the script keeps posting current price/profit so you can bailout when needed.
* `BUY_VALUE_USD` may be set to a positive value to specify the value of quote currency `QCOIN` to use in USD. If your quote currency balance is too low, the script will buy `QCOIN` on the market by selling currencies specified in `SOURCE_COINS` if you have sufficient balance in one of them. Set this to `0` to use `BUY_PERC` instead. If `BUY_VALUE_USD` is nonzero, `BUY_PERC` is ignored. Useful for automation or avoiding manual conversion.
* `SOURCE_COINS` are a comma-separated list of coins the script should try to sell in case your quote coin balance in `USD` is lower than `BUY_VALUE_USD`. The list may be empty. If so and your quote balance is lower than `BUY_VALUE_USD`, the script will fail. See [Quote restock](#quote-restock) for details.
* `PROMPT_OVERRIDE` may be set to `1` to enable prompts or `0` to disable them. Set it to `1` if you desire to make changes on startup.
* `DEFAULT_QCOIN` is the name of your quote asset (coin): the coin you wish to sell and later buy back with profit
* `DEFAULT_BUY_PERC` is the percentage of your quote asset balance you wish to sell. If prompts are not disabled, you can change the exact quote asset amount on startup
* `DEFAULT_SELL_TYPE` is the sell type you wish to use. See [Sell types](#sell-types) for details. The options are `LIMIT` and `MARKET`.
* `DEFAULT_PROFIT` is your desired profit. Mind that setting your profit very high may impair your ability to set a successful limit sell. You should use the `MARKET` sell type with high profits (such as >100%) or in a high-volatility scenario to ensure a sell.
* `DEFAULT_STOP_LEVEL` is the stop level (buy price percentage) to help you automatically manage risk. If your sell type is `MARKET` and the last traded price falls below this threshold, a market sell will trigger. If you use `LIMIT` and stop value is >-100, limit orders will be replaced with OCO orders with stop price at this level. A limit (low) price will be placed at 95% of this level. Must be lower than your profit.

## Quote restock

When using `BUY_VALUE_USD` you may not have enough value in `QCOIN` to sell. This feature, enabled by setting some coins in `SOURCE_COINS`, may let you automatically buy some quote currency to have enough in your wallet. For example, if you use BUSD as a stable wallet, you may want to put BUSD in `SOURCE_COINS` to restock on the quote coin with BUSD if your balance is too low. If the script can't buy enough `QCOIN` using any of `SOURCE_COINS`, the script will fail. Keep `SOURCE_COINS` empty to rather fail than attempt a restock.

If `BUY_VALUE_USD=0`, `BUY_PERC` will be used instead to derive a proportional quote amount. Beware, if your quote balance is very low, trades may fail on Binance's order limits (see [Binance order limits](#binance-order-limits)).

## Sell types

The script supports the following sell types to sell your coin with profit (default type is chosen with `DEFAULT_SELL_TYPE`):

* `LIMIT` only allows limit/OCO sell orders to be made. You are limited by Binance's percentual limits which the script calculates after buying your base coins. A limit/OCO sell will be made at the prices defined by your `STOP_LEVEL` and `PROFIT` or adjusted to meet Binance's limits. Market sells won't be attempted. OCO orders will be placed if you set `DEFAULT_STOP_LEVEL` higher than -100. The important takeaway is that your profit and loss limits may be adjusted to meet Binance's rules! The adjustment only occurs right after buying, so a sell either executes immediately or fails. Use this sell type for comfortable low to moderate volatility scenarios and day trading. In a very high volatility scenario your maker order might be skipped and you may even net a bigger loss than you'd wish to accept. If the `BAILOUT` feature is enabled
* `MARKET` only allows market sell orders to be made. You are **not** limited by Binance's profit limits but there is no guarantee that you get the profit you desire. The script makes a market sell if the last traded price exceeds your target profit or the expected loss drops below your `STOP_LEVEL`. You can additionally use the `BAILOUT` feature (see [Bailout](#bailout)) to get out fast. This sell type is safer for very high volatility markets but requires your attention. Monitor the price updates in the script's output and snipe your profit!

## Binance order limits

Binance imposes limits on order sizes. They limit total order value (notional value), base quantity and price (for limit orders). If you use the `LIMIT` sell type, the script will automatically adjust price targets if you exceed these limits but you may still hit the quantity filter, which also applies to `MARKET` orders. To avoid this, do not sell very small amounts of coins. If you automate this script, ensure your quote balance is high enough or use the `SOURCE_COINS` option with sufficient balance in one of the used source coins.

## How it works

The script first goes through the initial configuration which uses defaults from `.env` and if `PROMPT_OVERRIDE=1` prompts are displayed to let you override defaults. The script then displays your settings, starts an HTTP server listening for coin name hooks, and displays a prompt to enter a base coin name manually.

Once you enter the base coin (manually or via the HTTP hook) and the trading pair selected is available for trading, the script will forbid further coin names from being entered and immediately buy your base asset with your quote asset using a market buy.

Once the base asset is bought, the script will attempt to sell it using the provided sell type. Using the `LIMIT` sell type will either succeed or fail immediately. Using the `MARKET` sell type will start printing price updates alongside expected profits (profits are green, losses are red) and automatically sell upon hitting your profit/loss limits. Price updates are pulled from Binance's WebSockets API so you get fresh price updates as fast as possible.

With the `MARKET` sell type use `Ctrl+C` to stop the script and optionally sell right away if `BAILOUT` is enabled.

### Bailout

This feature is present if you set `BAILOUT=1`. Once the base asset is passed in and the script starts collecting market data, you can press `Ctrl+C` anytime to immediately sell all remaining base assets via market sell. Use this to bail out of unfavorable market conditions or take a lower profit.

If used with the `LIMIT` sell type, the script keeps running and posting price updates. You may press `Ctrl+C` to cancel your limit order and market sell immediately.

If the feature is enabled and you wish to stop the script without triggering a sell, please kill or suspend it instead. Remember to set `BAILOUT=0` in advance if you don't want this feature.

## Binance API latency

Binance hosts its API clusters in the far East, likely in the Tokyo AWS region. If you aren't living nearby, your latency to Binance's API might be unfavorable (400-800ms in Europe). To improve your latency, consider setting up a virtual machine near this region. An EC2 machine in the Tokyo AWS region only takes ~30ms to reach their servers.

## Future improvements

* Improved market analysis to better support day trading (possibly a related project)
