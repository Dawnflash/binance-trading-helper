# Binance trading helper

This python script creates a market buy order followed by a limit sell order with a set profit. This only works at binance.com.

Do not overuse, Binance only allows 100 orders per 10s. You might get rate limited otherwise.

## Requirements

* Python 3.6 or higher
* Requests module (install: `pip install requests`)

## Instructions

0. Create a binance.com API key (allow basic info and spot trading)
1. Copy `.env.example` to `.env` and fill in your Binance API key+secret
2. Override settings in `.env` if you wish, settings `DEFAULT_` may be overriden at runtime
3. Run `api.py` using Python 3 and respond to prompts
