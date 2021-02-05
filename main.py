#!/usr/bin/env python

""" Binance quick market buy+limit sell script
Author: Dawnflash

Ensure .env is created (use .env.example as a template)
Follow the prompts and good luck.
"""

import requests
from threading import Thread, Lock
from server import HTTPCoinAcceptor
from util import InvalidPair, Environment, SellStrategy, ffmt
from api import BinanceApi
from time import sleep


class MarketManager:
  def __init__(self, pairs: dict, qamount: float):
    self.qamount = qamount
    self.pairs = pairs
    self.mutex = Lock()
    self.done = False

  # True => success, False => repeat, Exception => abort
  def start(self, bcoin: str):
    self.mutex.acquire()
    if self.done:
      self.mutex.release()
      raise Exception('Market operation already executed!')
    if bcoin not in self.pairs:
      self.mutex.release()
      raise InvalidPair(f'Trading pair {bcoin}/{env.qcoin} not found')
    self.done = True
    self.mutex.release()

    api.set_pair(self.pairs[bcoin])
    with requests.Session() as session:
      # buy <bcoin> immediately at market price, get qty. and avg. price
      qty, price = api.buy_coin_market(session, self.qamount)
      # sell bought <bcoin> with <profit>% profit
      self.sell_coins(session, self.pairs[bcoin], qty, price)

  # sell <sqty> base coins on the market bought at <bprice>
  # target price: <tprice>
  # last market price: <lprice>
  # if executed return True, <executed qty>, otherwise False, 0
  def sell_coins_market(self, session: requests.Session, sqty: float,
                        tprice: float, bprice: float, lprice: float) -> (bool, float):
    eprofit = 100 * (lprice / bprice - 1)
    print(f'[INFO] Last market price is {api.qfmt(lprice)} {env.qcoin}' + \
          f'(expected profit: {eprofit}%)')
    # market sells only if last price exceeds target price
    if lprice > tprice:
      # initiate market sell
      qty, price = api.sell_coin_market(session, sqty)
      profit  = 100 * (price / bprice - 1)
      print(f'[MARKET SELL] executed with {profit}% profit')
      return True, qty
    else:
      print(f'[SKIP] Last market price is too low')
    return False, 0

  def sell_coins_limit(self, session: requests.Session, sqty: float,
                       tprice: float, mprice: float, bprice: float, limit: float) -> bool:
    max_profit = 100 * (limit / bprice - 1)
    print(f'[INFO] Current max limit price: {api.qfmt(limit)} {env.qcoin}' + \
          f'(max possible profit: {max_profit}%)')
    price = min(tprice, limit)
    # increase target profit if allowed and possible
    if env.inc_limit and limit > tprice:
      price = limit
      print(f'[INFO] Increasing target profit limit to {max_profit}%')
    # if we exceed the limit, take appropriate action
    if tprice > limit:
      if mprice > limit:
        # we do not accept the decreased profit, wait
        print(f'[SKIP] Maximum profit limit is too low')
        return False
      # we accept the decreased profit, take it
      print(f'[INFO] Decreasing target profit to maximum limit {max_profit}%')
    # initiate limit sell
    if api.sell_coin_limit(session, sqty, price):
      profit = 100 * (price / bprice - 1)
      print(f'[LIMIT SELL] executed at {price} limit (possible profit: {profit}%)')
      return True
    else:
      print(f'[ERROR] Limit sell failed, continuing...')
    return False

  def sell_coins(self, session: requests.Session,
                 pair: dict, bqty: float, buy_price: float):
    bcoin     = pair['baseAsset']
    tprice    = (1 + env.profit / 100) * buy_price
    mprice    = (1 + env.min_profit / 100) * buy_price
    orders    = 90 # maximum successful orders to make
    sell_bqty = env.sell_perc / 100 * bqty # bqty to sell at once

    while bqty > 0 and orders > 0:
      # sell the remaining coins if needed
      if bqty < sell_bqty or orders == 1:
        sell_bqty = bqty
      print(f'[INFO] Attempting to sell {sell_bqty} {bcoin} at {tprice} {env.qcoin} ' + \
            f'[{orders} orders left]')

      # sleep for 100ms not to overload the API
      sleep(0.1)

      if env.sell_strat != SellStrategy.MARKET:
        # fetch upper sell limit
        limit = api.sell_max_limit(session)
      if env.sell_strat != SellStrategy.LIMIT:
        # fetch last market price
        lprice = api.last_price(session)

      if env.sell_strat == SellStrategy.MARKET or env.sell_strat == SellStrategy.HYBRID:
        succ, qty = self.sell_coins_market(session, sell_bqty, tprice, buy_price, lprice)
        if succ:
          bqty -= qty
          orders -= 1
          continue
      if env.sell_strat == SellStrategy.LIMIT or env.sell_strat == SellStrategy.HYBRID:
        if self.sell_coins_limit(session, sell_bqty, tprice, mprice, buy_price, limit):
          bqty -= sell_bqty
          orders -= 1


# globals
env = Environment()
api = BinanceApi(env)


def coin_from_input(manager: MarketManager):
  while True:
    bcoin = ''
    while bcoin == '':
      try:
        bcoin = input('Enter base coin symbol (coin to buy and sell): ').upper()
      except Exception as e:
        print(str(e))
        return
    try:
      manager.start(bcoin)
    except Exception as e:
      print(str(e))


def coin_from_http(manager: MarketManager):
  acceptor = HTTPCoinAcceptor(manager, env.conn)
  acceptor.start()


def qamount_check(amt: float, bal: float, pairs: dict):
  if amt <= 0:
    raise ValueError(f'Error: cannot sell non-positive amount of {env.qcoin}')
  if amt > bal:
    raise ValueError(f'Error: you cannot sell more {env.qcoin} than you have!')
  if amt >= 0.95 * bal:
    print(f'Warning: you are trying to sell more than 95% of your {env.qcoin}.\n' + \
          'Attempting to buy that much might fail due to unexpected Binance errors!')
  notional_warn = []
  for asset, data in pairs.items():
    filt = data['filters'][3]
    if filt['applyToMarket'] and float(filt['minNotional']) >= amt:
      notional_warn.append(asset)
  if notional_warn:
    print('Warning: for the following base assets you are in breach of the minimum notional limit:')
    print(', '.join(notional_warn))
    print('A market buy order will fail for these assets')


def set_stdin(prompt: str, default):
  if not env.override:
    return default
  rt = input(prompt)
  return rt if rt else default


# main parameter setup
# return exchange info and quote amount to sell
def setup() -> (dict, float):
  p = f'Enter quote coin symbol (coin to trade for) [default: {env.qcoin}]: '
  env.qcoin = set_stdin(p, env.qcoin).upper()

  with requests.Session() as session:
    info      = api.exchange_info(session)
    qbalance  = api.coin_balance(session)

  symbols = api.quote_symbols(info)
  def_qty = env.buy_perc / 100 * qbalance

  p = f'Enter {env.qcoin} amount to sell [default: {ffmt(def_qty)} ({env.buy_perc}%)]: '
  qamount = float(set_stdin(p, def_qty))
  qamount_check(qamount, qbalance, symbols)

  p = f'Enter desired profit in % [default: {env.profit}]: '
  env.profit = float(set_stdin(p, env.profit))

  if env.profit <= 0:
    print('Warning: you have set a non-positive profit. Proceeding may net you a loss!')
  if env.profit >= 100 and env.sell_strat == SellStrategy.LIMIT:
    print('Warning: you have set a high profit, limit orders may fail.\n' + \
          'Consider using MARKET or HYBRID strategy.')
  env.min_profit = min(env.min_profit, env.profit)

  p = f'Enter percentage of base coin to sell at once [default: {env.sell_perc}]: '
  env.sell_perc = float(set_stdin(p, env.sell_perc))
  if not 0 < env.sell_perc <= 100:
    raise ValueError('Error: cannot sell more than 100% at once')
  if env.sell_perc < 25:
    print(f'Warning: selling {env.sell_perc}% at once might be too slow and/or ineffective')

  p = f'Enter sell strategy (LIMIT|MARKET|HYBRID) [default: {env.sell_strat.name}]: '
  env.sell_strat = SellStrategy(set_stdin(p, env.sell_strat))

  if env.sell_strat != SellStrategy.MARKET:
    p = f'Enter minimum acceptable profit in % [default: {env.min_profit}]: '
    env.min_profit = float(set_stdin(p, env.min_profit))

    if env.min_profit > env.profit:
      raise ValueError('Error: minimum allowed profit cannot exceed target profit!')

    p = f'Allow increasing target profit to hit Binance sell limit? ' + \
        f'[0|1, default: {int(env.inc_limit)}]: '
    env.inc_limit = bool(set_stdin(p, env.inc_limit))

  print('---- SELECTED OPTIONS ----')
  print(f'Selected quote coin: {env.qcoin}')
  print(f'Selected quote amount to sell: {qamount} {env.qcoin} (available: {qbalance} {env.qcoin})')
  print(f'Selected target profit: {env.profit}%')
  print(f'Selected sell strategy: {env.sell_strat.name}')
  if env.sell_strat != SellStrategy.MARKET:
    print(f'Selected minimum acceptable profit: {env.min_profit}%')
    print(f'Allow profit limit increase: {int(env.inc_limit)}')
  print('--------------------------')
  return symbols, qamount


def main():
  print('### Dawn\'s Binance market tool ###')
  if env.override:
    print('Want to skip prompts? Set DEFAULT_OVERRIDE to 0!')

  # initialize Market Manager (prepare everything)
  manager = MarketManager(*setup())

  # Start a HTTP server listening for coin signals
  http_thr  = Thread(target=coin_from_http, args = (manager,), daemon=True)
  print(f'Starting HTTP listener at {env["SERVER_HOST"]}:{env["SERVER_PORT"]}')
  http_thr.start()
  # Fetch coin info from stdin
  coin_from_input(manager)


if __name__ == '__main__':
  main()
