#!/usr/bin/env python

""" Binance quick market buy+limit sell script
Author: Dawnflash

Ensure .env is created (use .env.example as a template)
Follow the prompts and good luck.
"""

import requests
from threading import Thread, Lock, Condition
from server import HTTPCoinAcceptor
from util import InvalidPair, Environment, SellStrategy, CColors, CException, ffmt
from api import BinanceApi


# Primary logic structure, manages trades
class MarketManager:
  def __init__(self, pairs: dict, qqty: float):
    # all possible */<quote coin> pairs
    self.pairs = pairs
    # pre-buy data
    self.qqty = qqty
    # after-buy data
    self.bqty   = 0 # total bought qty
    self.bprice = 0 # mean buy price
    self.tprice = 0 # target sell price
    self.mprice = 0 # minimum sell price (if limited)
    self.sprice = 0 # stop price
    self.sqty   = 0 # sell qty (at once)
    # coin locking
    self.m1 = Lock()
    self.locked = False
    # worker notification
    self.m2 = Lock()
    self.cv = Condition(self.m2)
    self.ready = False

  # lock in a trade pair and notify main thread
  # InvalidPair => bad pair/try again, Exception => abort
  # on success notify a waiting thread to call `start`
  def lock(self, bcoin: str):
    self.m1.acquire()
    # reject if locked
    if self.locked:
      self.m1.release()
      raise CException('Market operation is already running!')
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

  # start trading, only call this method after locking in a pair
  def start(self):
    CColors.iprint(f'Market manager started with pair {api.pair["symbol"]}')
    with requests.Session() as session:
      # buy <bcoin> immediately at market price, get qty. and avg. price
      s, self.bqty, self.bprice = api.buy_coin_market(session, self.qqty)
      if not s:
        return
      self.tprice = (1 + env.profit / 100) * self.bprice
      self.mprice = (1 + env.min_profit / 100) * self.bprice
      self.sprice = (1 + env.stop / 100) * self.bprice
      self.sqty   = env.sell_perc / 100 * self.bqty
      # sell bought <bcoin> with <profit>% profit
      self.sell_coins(session)

  # report on a successful market sell
  # <sprice>: average sell price
  def sell_market_report(self, sprice: float):
    profit = 100 * (sprice / self.bprice - 1)
    if profit >= 0:
      CColors.cprint(f'[MARKET SELL PROFIT] {profit:.2f}%', CColors.OKGREEN)
    else:
      CColors.cprint(f'[MARKET SELL LOSS] {profit:.2f}%', CColors.FAIL)

  # sell coins using market sell
  # <lprice>: last market price
  # <avg> average market price
  # return success and executed qty (0 if failed)
  def sell_coins_market(self, session: requests.Session,
                        lprice: float, avg: float) -> (bool, float):
    # sell if forced or profit/loss limits are triggered
    if lprice > self.tprice or lprice < self.sprice:
      lo, hi = api.qty_bound(avg, True)
      if not lo <= self.sqty <= hi:
        if lo <= self.bqty <= hi:
          self.sqty = self.bqty
        else:
          CColors.wprint('Sell quantity out of allowed bounds, cannot sell!')
          return False
      # initiate market sell
      s, qty, price = api.sell_coin_market(session, self.sqty)
      if not s:
        return False, 0
      self.sell_market_report(price)
      return True, qty
    return False, 0

  # sell coins using a limit or OCO sell
  # <avg> average market price
  # return success
  def sell_coins_limit(self, session: requests.Session, avg: float) -> bool:
    lo, hi = api.price_bound(avg)
    price = min(self.tprice, hi)
    # if we exceed the limit try to decrease profit or fail
    if self.tprice > hi:
      if self.mprice > hi:
        # we do not accept the decreased profit, wait
        return False
      # accept the decreased profit
      max_profit = 100 * (hi / self.bprice - 1)
      CColors.iprint(f'Decreasing target profit to {max_profit:.2f}%')
    if price < lo:
      return False
    ql, qh = api.qty_bound(price)
    if not ql <= self.sqty <= qh:
      if ql <= self.bqty <= qh:
        self.sqty = self.bqty
      else:
        CColors.wprint('Sell quantity out of allowed bounds, cannot sell!')
        return False
    # try OCO if suitable
    if env.stop > -100 and api.pair['ocoAllowed']:
      if not api.sell_coin_oco(session, self.sqty, price, self.sprice):
        return False
      profit = 100 * (price / self.bprice - 1)
      CColors.cprint(f'[OCO SELL] target profit: {profit:.2f}%, max loss: {-env.stop:.2f}%',
                      CColors.OKGREEN)
      return True
    # initiate limit sell
    if not api.sell_coin_limit(session, self.sqty, price):
      return False
    profit = 100 * (price / self.bprice - 1)
    CColors.cprint(f'[LIMIT SELL] target profit: {profit:.2f}%', CColors.OKGREEN)
    return True

  # coin selling logic
  def sell_coins(self, session: requests.Session):
    bcoin   = api.pair['baseAsset']
    orders  = 90  # maximum successful orders to make
    avg     = 0   # average traded price
    lprice  = 0   # last traded price

    try:
      while self.bqty > 0 and orders > 0:
        # sell the remaining coins if needed
        if self.bqty < self.sqty or orders == 1:
          self.sqty = self.bqty

        # fetch average and last traded price
        avg     = api.avg_price(session)
        lprice  = api.last_price(session)

        # calculate estimated profit
        eprofit = 100 * (lprice / self.bprice - 1)
        if eprofit >= 0:
          CColors.cprint(f'[+{eprofit:.2f}%] 1 {bcoin} = {api.qfmt(lprice)} {env.qcoin}',
                          CColors.OKGREEN)
        else:
          CColors.cprint(f'[{eprofit:.2f}%] 1 {bcoin} = {api.qfmt(lprice)} {env.qcoin}',
                          CColors.FAIL)

        if env.sell_strat == SellStrategy.MARKET or env.sell_strat == SellStrategy.HYBRID:
          succ, qty = self.sell_coins_market(session, lprice, avg)
          if succ:
            self.bqty -= qty
            orders -= 1
            continue
        if env.sell_strat == SellStrategy.LIMIT or env.sell_strat == SellStrategy.HYBRID:
          if self.sell_coins_limit(session, avg):
            self.bqty -= self.sqty
            orders -= 1
    except KeyboardInterrupt:
      if env.bailout:
        # Sell everything on market immediately
        CColors.wprint('Selling on market immediately!')
        s, _, p = api.sell_coin_market(session, self.bqty)
        if s:
          self.sell_market_report(p)


# globals
env = Environment()
api = BinanceApi(env)


# fetch a coin name from stdin
def coin_from_stdin(manager: MarketManager):
  p = CColors.cstr('Enter base coin symbol (coin to buy and sell): ',
                    CColors.WARNING)
  while True:
    try:
      bcoin = input(p).upper()
    except Exception:
      break
    if not bcoin:
      continue
    try:
      manager.lock(bcoin)
      return
    except InvalidPair as e:
      print(str(e))
    except Exception as e:
      print(str(e))
      break
  with manager.cv:
    manager.cv.notify()


# start an HTTP server to listen for a coin name
def coin_from_http(manager: MarketManager):
  acceptor = HTTPCoinAcceptor(manager, env.conn)
  acceptor.start()


# prompt user for an override or return a default
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
  if qqty <= 0:
    raise CException(f'Cannot sell non-positive amount of {env.qcoin}')
  if qqty > qbalance:
    raise CException(f'Cannot sell more {env.qcoin} than you have!')

  p = f'Enter percentage of base coin to sell at once [default: {env.sell_perc}]: '
  env.sell_perc = float(set_stdin(p, env.sell_perc))
  if not 0 < env.sell_perc <= 100:
    raise CException('Error: cannot sell more than 100% at once')
  if env.sell_perc < 25:
    CColors.wprint(f'Selling {env.sell_perc:.2f}% at once might be too slow and/or ineffective')

  p = f'Enter sell strategy (LIMIT|MARKET|HYBRID) [default: {env.sell_strat.name}]: '
  env.sell_strat = SellStrategy(set_stdin(p, env.sell_strat))

  p = f'Enter desired profit in % [default: {env.profit:.2f}]: '
  env.profit = float(set_stdin(p, env.profit))

  if env.profit <= 0:
    CColors.wprint('You have set a non-positive profit. Proceeding may net you a loss!')
  if env.profit >= 100 and env.sell_strat == SellStrategy.LIMIT:
    CColors.wprint('You have set a high profit, limit orders may fail.\n' + \
          'Consider using MARKET or HYBRID strategy.')
  env.min_profit = min(env.min_profit, env.profit)

  if env.sell_strat != SellStrategy.MARKET:
    p = f'Enter minimum acceptable profit in % [default: {env.min_profit:.2f}]: '
    env.min_profit = float(set_stdin(p, env.min_profit))

    if env.min_profit > env.profit:
      raise CException('Minimum allowed profit cannot exceed target profit!')

  p = 'Enter stop profit - stop limit price for limit orders (OCO) ' + \
      f'or stop market sells to mitigate loss [default: {env.stop:.2f}]: '
  env.stop = float(set_stdin(p, env.stop))
  if not -100 <= env.stop < env.min_profit:
    raise CException('Stop percentage must be lower than profits!')

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
  if env.override:
    print('Want to skip prompts? Set DEFAULT_OVERRIDE to 0!')

  # initialize Market Manager (prepare everything)
  manager = MarketManager(*setup())

  # Start coin name listeners: stdin and HTTP
  http_thr  = Thread(target=coin_from_http, args = (manager,), daemon=True)
  stdin_thr = Thread(target=coin_from_stdin, args = (manager,), daemon=True)
  print(f'Starting HTTP listener at {env["SERVER_HOST"]}:{env["SERVER_PORT"]}')
  http_thr.start()
  stdin_thr.start()

  if env.bailout:
    print('Bailout enabled: once trading starts, press Ctrl+C to sell immediately')

  # wait for coin lock
  try:
    with manager.cv:
      manager.cv.wait_for(lambda: manager.ready)
  except:
    return

  # start trading
  manager.start()


if __name__ == '__main__':
  main()
