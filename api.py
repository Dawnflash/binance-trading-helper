#!/usr/bin/env python

""" Binance quick market buy+limit sell script
Author: Dawnflash

Ensure .env is created (use .env.example as a template)
Follow the prompts and good luck.
"""

import requests
import hmac
import hashlib
import urllib
from server import HTTPCoinAcceptor
from util import InvalidPair, Environment, ffloat, tstamp, bencode
from threading import Thread, Lock


# global environment
env = Environment()


# sign <val> dictionary with SHA256 HMAC (pass your request parameters to <val>)
def sign(val: dict) -> str:
  bkey, bval = bencode(env['BINANCE_API_SECRET']), bencode(urllib.parse.urlencode(val))
  return hmac.new(bkey, bval, hashlib.sha256).hexdigest()


# make a Binance API request
def api_req(session: requests.Session,
            method: str,
            uri: str,
            params: dict = {},
            headers: dict = {},
            signed: bool = True) -> (dict, dict):
  headers['Accept'] = 'application/json'

  if signed:
    headers['X-MBX-APIKEY'] = env['BINANCE_API_KEY']
    params['signature'] = sign(params)

  url = urllib.parse.urljoin(env['BASE_API_URL'], uri)
  resp = session.request(method, url, params=params, headers=headers)

  # check for issues
  if resp.status_code == 418: # IP ban
    raise Exception(f"Your IP is banned for {resp.headers['Retry-After']} seconds. Wait!")
  if resp.status_code == 429: # Rate limiting
    raise Exception(f"Your IP is rate limited for {resp.headers['Retry-After']} seconds. Wait!")
  if not resp.ok: # other issues
    raise Exception(f'Request for {url} failed\n{resp.status_code}: {resp.json()}')
  return resp.headers, resp.json()


# return exchange info
def exchange_info(session: requests.Session) -> dict:
  _, resp = api_req(session, 'GET', 'exchangeInfo', signed=False)
  return resp


# get valid exchange symbols for a give quote coin
def quote_symbols(exinfo: dict) -> dict:
  symbols = {}
  if 'symbols' not in exinfo:
    raise InvalidPair('Symbols not found in exchange info')
  for symbol in exinfo['symbols']:
    if symbol['quoteAsset'] == env.qcoin:
      symbols[symbol['baseAsset']] = symbol
  if not symbols:
    raise InvalidPair(f'No matching trading pairs found for quote asset {env.qcoin}')
  return symbols


# get <qcoin> balance in your spot account
def coin_balance(session: requests.Session) -> float:
  _, resp = api_req(session, 'GET', 'account', {'timestamp': tstamp()})
  if 'balances' not in resp:
    raise Exception(f'Failed to query user data, bailing out')
  for balance in resp['balances']:
    if balance['asset'] == env.qcoin:
      bf, bl = float(balance['free']), float(balance['locked'])
      print(f'Your free balance for {env.qcoin} is {ffloat(bf)} (locked: {ffloat(bl)})')
      return bf
  raise Exception(f'Coin {env.qcoin} not found in your account. ' + \
                  'Make sure to choose a valid coin!')


# print buy order status and return average fill price
def order_status(bcoin: str, resp: dict) -> (float, float):
  bqty, qqty = float(resp["executedQty"]), float(resp["cummulativeQuoteQty"])
  print(f'Order status: {resp["status"]}')
  print(f'Bought {ffloat(bqty)} {bcoin} using {ffloat(qqty)} {env.qcoin}')
  print('Fills:')
  price = 0
  for fill in resp['fills']:
    p, q, c = float(fill['price']), float(fill['qty']), float(fill['commission'])
    price += p * q / bqty
    print(f'  {ffloat(q)} {bcoin} at {ffloat(p)} {env.qcoin} ' + \
          f'(fee: {ffloat(c)} {fill["commissionAsset"]})')
  print(f'Average buy price: {ffloat(price)} {env.qcoin}')
  if (price == 0):
    raise Exception(f'Total price of bought {bcoin} seems to be zero. ' + \
                    'Something is wrong! Check Binance manually!')
  return bqty, price


# buy <bcoin> with <qamount> of <qcoin> at market price
# return amount of <bcoin> purchased (executed) and average (weighted) price
def buy_coin_market(session: requests.Session, bcoin: str, qamount: float) -> (float, float):
  print(f'Buying {bcoin} using {ffloat(qamount)} {env.qcoin} at market price...')

  params = {
    'symbol': bcoin + env.qcoin,
    'side': 'BUY',
    'type': 'MARKET',
    'quoteOrderQty': ffloat(qamount), # buy with <qamount> of <qcoin>
    'timestamp': tstamp()
  }
  _, resp = api_req(session, 'POST', 'order', params)

  return order_status(bcoin, resp)


# sell <bamount> of <bcoin>
def sell_coin_limit(session: requests.Session, bcoin: str, bamount: float, price: float):
  limit = (100 + env.profit) / 100 * price
  print(f'Selling {ffloat(bamount)} {bcoin} for {env.qcoin} ' + \
        f'with {ffloat(env.profit)}% profit at price limit {ffloat(limit)}...')

  params = {
    'symbol': bcoin + env.qcoin,
    'side': 'SELL',
    'type': 'LIMIT',
    'timeInForce': 'GTC', # good till cancelled
    'quantity': ffloat(bamount),
    'price': ffloat(limit),
    'timestamp': tstamp()
  }
  _, resp = api_req(session, 'POST', 'order', params)
  print(f'Executed limit sell order (status: {resp["status"]})')
  print(f"Check the {bcoin}/{env.qcoin} trading pair on Binance now!")


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
    with requests.Session() as session:
      # buy <bcoin> immediately at market price, get qty. and avg. price
      qty, price = buy_coin_market(session, bcoin, self.qamount)
      # sell bought <bcoin> with <profit>% profit
      sell_coin_limit(session, bcoin, qty, price)
      return True


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
    except InvalidPair as e:
      print(str(e))


def coin_from_http(manager: MarketManager):
  acceptor = HTTPCoinAcceptor(manager, env.conn)
  acceptor.start()


def main():
  iqcoin = input(f'Enter quote coin symbol (coin to trade for) [default: {env.qcoin}]: ')
  env.qcoin = env.qcoin if iqcoin == '' else iqcoin.upper()

  with requests.Session() as session:
    info      = exchange_info(session)
    qbalance  = coin_balance(session)
  
  symbols = quote_symbols(info)

  qamount = input(f'Enter {env.qcoin} amount to sell [default: {ffloat(qbalance)}]: ')
  qamount = env.qbalperc / 100 * qbalance if qamount == '' else float(qamount)

  iprofit = input(f'Enter desired profit in % [default: {ffloat(env.profit)}]: ')
  env.profit = env.profit if iprofit == '' else float(iprofit)

  manager = MarketManager(symbols, qamount)

  input_thr = Thread(target=coin_from_input, args = (manager,))
  http_thr  = Thread(target=coin_from_http, args = (manager,))
  http_thr.daemon = True

  input_thr.start()
  http_thr.start()

  input_thr.join()
  http_thr.join(1)


if __name__ == '__main__':
  main()
