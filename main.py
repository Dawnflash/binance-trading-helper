#!/usr/bin/env python

""" Binance quick market buy+limit sell script
Author: Dawnflash

Ensure .env is created (use .env.example as a template)
Follow the prompts and good luck.
"""

import requests
from threading import Thread, Lock, Condition
from server import HTTPCoinAcceptor
from util import InvalidPair, Environment, SellStrategy, ffmt
from api import BinanceApi
from time import sleep


class MarketManager:
  def __init__(self, pairs: dict, qqty: float):
    self.qqty = qqty
    self.pairs = pairs
    # coin locking
    self.m1 = Lock()
    self.locked = False
    # worker notification
    self.m2 = Lock()
    self.cv = Condition(self.m2)
    self.ready = False

  # lock in a coin and notify main thread
  # InvalidPair => bad coin/try again, Exception => abort
  def lock(self, bcoin: str):
    self.m1.acquire()
    # reject if locked
    if self.locked:
      self.m1.release()
      raise Exception('Market operation is already running!')
    # retry if bad coin is turned in
    if bcoin not in self.pairs:
      self.m1.release()
      raise InvalidPair(f'Trading pair {bcoin}/{env.qcoin} not found')
    self.locked = True
    self.m1.release()
    # lock in a trading pair
    api.set_pair(self.pairs[bcoin])
    # notify worker
    with self.cv:
      self.ready = True
      self.cv.notify()

  def start(self):
    print(f'Market manager started with pair {api.pair["symbol"]}')
    with requests.Session() as session:
      # buy <bcoin> immediately at market price, get qty. and avg. price
      qty, price = api.buy_coin_market(session, self.qqty)
      # sell bought <bcoin> with <profit>% profit
      self.sell_coins(session, qty, price)

  # sell <sqty> base coins on the market bought at <bprice>
  # target price: <tprice>
  # last market price: <lprice>
  # if executed return True, <executed qty>, otherwise False, 0
  def sell_coins_market(self, session: requests.Session, sqty: float,
                        bprice: float) -> (bool, float):
    lprice = api.last_price(session)
    eprofit = 100 * (lprice / bprice - 1)
    print(f'[INFO] Last market price is {api.qfmt(lprice)} {env.qcoin}' + \
          f'(expected profit: {eprofit:.2f}%)')

    # market sells only if last price exceeds target price
    if eprofit > env.profit or eprofit <= env.stop:
      # initiate market sell
      qty, price = api.sell_coin_market(session, sqty)
      profit  = 100 * (price / bprice - 1)
      print(f'[MARKET SELL] executed with {profit:.2f}% profit')
      return True, qty
    else:
      print('[SKIP] Last market price is too low')
    return False, 0

  # sell coins using a limit sell
  # <sqty> sell quantity
  # <prices> {buy price, target price, minimum acceptable price, stop price, average price}
  def sell_coins_limit(self, session: requests.Session, sqty: float, prices: tuple) -> bool:
    bprice, tprice, mprice, sprice, avg = prices
    lo, hi = api.price_bound(avg)
    max_profit = 100 * (hi / bprice - 1)
    print(f'[INFO] Current max limit price: {api.qfmt(hi)} {env.qcoin} ' + \
          f'(max possible profit: {max_profit:.2f}%)')
    price = min(tprice, hi)
    # if we exceed the limit, take appropriate action
    if tprice > hi:
      if mprice > hi:
        # we do not accept the decreased profit, wait
        print(f'[SKIP] Maximum profit limit is too low')
        return False
      # we accept the decreased profit, take it
      print(f'[INFO] Decreasing target profit to maximum limit {max_profit:.2f}%')
    if price < lo:
      print('[ERROR] Price too low')
      return False
    ql, qh = api.qty_bound(price)
    if not ql <= sqty <= qh:
      print('[ERROR] Sell amount out of allowed bounds')
      return False
    # try OCO if suitable
    if env.stop > -100 and api.pair['ocoAllowed']:
      if api.sell_coin_oco(session, sqty, price, sprice):
        profit = 100 * (price / bprice - 1)
        print(f'[OCO SELL] executed at {api.qfmt(price)} limit (max profit: {profit:.2f}%), ' + \
              f'stop {api.qfmt(price)} (max loss: {-env.stop:.2f}%)')
        return True
      else:
        print('[ERROR] OCO sell failed, continuing...')
        return False
    # initiate limit sell
    if api.sell_coin_limit(session, sqty, price):
      profit = 100 * (price / bprice - 1)
      print(f'[LIMIT SELL] executed at {api.qfmt(price)} limit (possible profit: {profit:.2f}%)')
      return True
    else:
      print('[ERROR] Limit sell failed, continuing...')
    return False

  def sell_coins(self, session: requests.Session, bqty: float, buy_price: float):
    bcoin     = api.pair['baseAsset']
    tprice    = (1 + env.profit / 100) * buy_price
    mprice    = (1 + env.min_profit / 100) * buy_price
    sprice = (1 + env.stop / 100) * buy_price
    orders    = 90 # maximum successful orders to make
    sell_bqty = env.sell_perc / 100 * bqty # bqty to sell at once

    while bqty > 0 and orders > 0:
      # sell the remaining coins if needed
      if bqty < sell_bqty or orders == 1:
        sell_bqty = bqty
      print(f'[INFO] Attempting to sell {sell_bqty} {bcoin} at {api.qfmt(tprice)} {env.qcoin} ' + \
            f'[{orders} orders left]')

      # sleep a little
      sleep(env.sleep)

      # fetch average price
      avg = api.avg_price(session)

      if env.sell_strat == SellStrategy.MARKET or env.sell_strat == SellStrategy.HYBRID:
        lo, hi = api.qty_bound(avg, True)
        if not lo <= sell_bqty <= hi:
          if lo <= bqty <= hi:
            sell_bqty = bqty
          else:
            print('[ERROR] Cannot market sell right now (base amount out of bounds)')
            continue
        succ, qty = self.sell_coins_market(session, sell_bqty, buy_price)
        if succ:
          bqty -= qty
          orders -= 1
          continue
      if env.sell_strat == SellStrategy.LIMIT or env.sell_strat == SellStrategy.HYBRID:
        if self.sell_coins_limit(session, sell_bqty, (buy_price, tprice, mprice, sprice, avg)):
          bqty -= sell_bqty
          orders -= 1


# globals
env = Environment()
api = BinanceApi(env)


def coin_from_stdin(manager: MarketManager):
  while True:
    try:
      bcoin = input('Enter base coin symbol (coin to buy and sell): ').upper()
    except Exception:
      break
    if not bcoin:
      continue
    try:
      manager.lock(bcoin)
      break
    except InvalidPair as e:
      print(str(e))
    except Exception as e:
      print(str(e))
      break
  with manager.cv:
    manager.cv.notify()


def coin_from_http(manager: MarketManager):
  acceptor = HTTPCoinAcceptor(manager, env.conn)
  acceptor.start()


def qqty_check(amt: float, bal: float, pairs: dict):
  if amt <= 0:
    raise ValueError(f'Error: cannot sell non-positive amount of {env.qcoin}')
  if amt > bal:
    raise ValueError(f'Error: you cannot sell more {env.qcoin} than you have!')


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

  p = f'Enter {env.qcoin} amount to sell [default: {ffmt(def_qty)} ({env.buy_perc:.2f}%)]: '
  qqty = float(set_stdin(p, def_qty))
  qqty_check(qqty, qbalance, symbols)

  p = f'Enter percentage of base coin to sell at once [default: {env.sell_perc}]: '
  env.sell_perc = float(set_stdin(p, env.sell_perc))
  if not 0 < env.sell_perc <= 100:
    raise ValueError('Error: cannot sell more than 100% at once')
  if env.sell_perc < 25:
    print(f'Warning: selling {env.sell_perc:.2f}% at once might be too slow and/or ineffective')

  p = f'Enter sell strategy (LIMIT|MARKET|HYBRID) [default: {env.sell_strat.name}]: '
  env.sell_strat = SellStrategy(set_stdin(p, env.sell_strat))

  p = f'Enter desired profit in % [default: {env.profit:.2f}]: '
  env.profit = float(set_stdin(p, env.profit))

  if env.profit <= 0:
    print('Warning: you have set a non-positive profit. Proceeding may net you a loss!')
  if env.profit >= 100 and env.sell_strat == SellStrategy.LIMIT:
    print('Warning: you have set a high profit, limit orders may fail.\n' + \
          'Consider using MARKET or HYBRID strategy.')
  env.min_profit = min(env.min_profit, env.profit)

  if env.sell_strat != SellStrategy.MARKET:
    p = f'Enter minimum acceptable profit in % [default: {env.min_profit:.2f}]: '
    env.min_profit = float(set_stdin(p, env.min_profit))

    if env.min_profit > env.profit:
      raise ValueError('Error: minimum allowed profit cannot exceed target profit!')

  p = 'Enter stop profit - stop limit price for limit orders (OCO) ' + \
      f'or stop market sells to mitigate loss [default: {env.stop:.2f}]: '
  env.stop = float(set_stdin(p, env.stop))
  if not -100 <= env.stop < env.min_profit:
    raise ValueError('stop percentage must go below your profit limits!')

  print('---- SELECTED OPTIONS ----')
  print(f'Selected quote coin: {env.qcoin}')
  print(f'Selected quote amount to sell: {ffmt(qqty)} {env.qcoin} (available: {ffmt(qbalance)} {env.qcoin})')
  print(f'Selected target profit: {env.profit:.2f}%')
  print(f'Selected stop percentage: {env.stop:.2f}%')
  print(f'Selected sell strategy: {env.sell_strat.name}')
  if env.sell_strat != SellStrategy.MARKET:
    print(f'Selected minimum acceptable profit: {env.min_profit:.2f}%')
  print('--------------------------')
  return symbols, qqty


def main():
  print('### Dawn\'s Binance market tool ###')
  if env.override:
    print('Want to skip prompts? Set DEFAULT_OVERRIDE to 0!')

  # initialize Market Manager (prepare everything)
  manager = MarketManager(*setup())

  # Start a HTTP server listening for coin signals
  http_thr  = Thread(target=coin_from_http, args = (manager,), daemon=True)
  stdin_thr = Thread(target=coin_from_stdin, args = (manager,), daemon=True)
  print(f'Starting HTTP listener at {env["SERVER_HOST"]}:{env["SERVER_PORT"]}')
  http_thr.start()
  # Start stdin listener
  stdin_thr.start()

  # wait for coin lock
  try:
    with manager.cv:
      manager.cv.wait_for(lambda: manager.ready)
  except:
    return
  manager.start()


if __name__ == '__main__':
  main()
