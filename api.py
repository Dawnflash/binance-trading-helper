#!/usr/bin/env python

""" Binance quick market buy+limit sell script
Author: Dawnflash

Ensure .env is created (use .env.example as a template)
Follow the prompts and good luck.
"""

import requests
import os
import sys
import time
import hmac
import hashlib
import urllib
from server import HTTPCoinAcceptor
from threading import Thread, Lock


base_url = 'https://api.binance.com/api/v3/' # base Binance API URL
env_path = '.env' # path to the environment file containing API keys
server_host = 'localhost'
server_port = 1337

# defaults (may be overriden at runtime)
qcoin = 'BTC' # quote coin (what are we trading for, uppercase)
profit = 400 # [%] target profit (percentage of buy price)


# read file at <path> and extract envvars from it
def get_env_data_as_dict(path: str) -> dict:
  with open(path, 'r') as f:
    return dict(tuple(line.replace('\n', '').split('=')) for line
      in f.readlines() if not line.startswith('#'))


# format float to 7 decimals with stripped zeros
def ffloat(val: float) -> str:
  sv = f'{val:.7f}'.rstrip('0')
  return sv[:-1] if sv.endswith('.') else sv


# current timestamp in milliseconds
def tstamp() -> int:
  return time.time_ns() // 1000000


# UTF-8 string to bytes
def bencode(val: str) -> bytes:
  return bytes(val, 'UTF-8')


# sign <val> dictionary with SHA256 HMAC (pass your request parameters to <val>)
def sign(val: dict) -> str:
  bkey, bval = bencode(env['BINANCE_API_SECRET']), bencode(urllib.parse.urlencode(val))
  return hmac.new(bkey, bval, hashlib.sha256).hexdigest()


# make a Binance API request
def api_req(session: requests.Session, method: str, uri: str, params: dict = {}, headers: dict = {}) -> (dict, dict):
  headers['Accept'] = 'application/json'
  headers['X-MBX-APIKEY'] = env['BINANCE_API_KEY']

  params['signature'] = sign(params)

  url = urllib.parse.urljoin(base_url, uri)
  resp = session.request(method, url, params=params, headers=headers)

  # check for issues
  if resp.status_code == 418: # IP ban
    raise Exception(f"Your IP is banned for {resp.headers['Retry-After']} seconds. Wait!")
  if resp.status_code == 429: # Rate limiting
    raise Exception(f"Your IP is rate limited for {resp.headers['Retry-After']} seconds. Wait!")
  if not resp.ok: # other issues
    raise Exception(f'Request for {url} failed\n{resp.status_code}: {resp.json()}')
  return resp.headers, resp.json()


# get <qcoin> balance in your spot account
def coin_balance(session: requests.Session) -> float:
  _, resp = api_req(session, 'GET', 'account', {'timestamp': tstamp()})
  if 'balances' not in resp:
    raise Exception(f'Failed to query user data, bailing out')
  for balance in resp['balances']:
    if balance['asset'] == qcoin:
      bf, bl = float(balance['free']), float(balance['locked'])
      print(f'Your free balance for {qcoin} is {ffloat(bf)} (locked: {ffloat(bl)})')
      return bf
  raise Exception(f'Coin {qcoin} not found in your account. Make sure to choose a valid coin!')


# print buy order status and return average fill price
def order_status(bcoin: str, resp: dict) -> (float, float):
  bqty, qqty = float(resp["executedQty"]), float(resp["cummulativeQuoteQty"])
  print(f'Order status: {resp["status"]}')
  print(f'Bought {ffloat(bqty)} {bcoin} using {ffloat(qqty)} {qcoin}')
  print('Fills:')
  price = 0
  for fill in resp['fills']:
    p, q, c = float(fill['price']), float(fill['qty']), float(fill['commission'])
    price += p * q / bqty
    print(f'  {ffloat(q)} {bcoin} at {ffloat(p)} {qcoin} (fee: {ffloat(c)} {fill["commissionAsset"]})')
  print(f'Average buy price: {ffloat(price)} {qcoin}')
  if (price == 0):
    raise Exception(f'Total price of bought {bcoin} seems to be zero. Something is wrong! Check Binance manually!')
  return bqty, price


# buy <bcoin> with <qamount> of <qcoin> at market price
# return amount of <bcoin> purchased (executed) and average (weighted) price
def buy_coin_market(session: requests.Session, bcoin: str, qamount: float) -> (float, float):
  print(f'Buying {bcoin} using {ffloat(qamount)} {qcoin} at market price...')

  params = {
    'symbol': bcoin + qcoin,
    'side': 'BUY',
    'type': 'MARKET',
    'quoteOrderQty': ffloat(qamount), # buy with <qamount> of <qcoin>
    'timestamp': tstamp()
  }
  _, resp = api_req(session, 'POST', 'order', params)

  return order_status(bcoin, resp)


# sell <bamount> of <bcoin>
def sell_coin_limit(session: requests.Session, bcoin: str, bamount: float, price: float):
  limit = (100 + profit) / 100 * price
  print(f'Selling {ffloat(bamount)} {bcoin} for {qcoin} with {ffloat(profit)}% profit at price limit {ffloat(limit)}...')

  params = {
    'symbol': bcoin + qcoin,
    'side': 'SELL',
    'type': 'LIMIT',
    'timeInForce': 'GTC', # good till cancelled
    'quantity': ffloat(bamount),
    'price': ffloat(limit),
    'timestamp': tstamp()
  }
  _, resp = api_req(session, 'POST', 'order', params)
  print(f'Executed limit sell order (status: {resp["status"]})')
  print(f"Check the {bcoin}/{qcoin} trading pair on Binance now!")


class MarketManager:
  def __init__(self, qamount: float):
    self.qamount = qamount
    self.mutex = Lock()
    self.done = False

  def start(self, bcoin: str):
    self.mutex.acquire()
    if self.done:
      self.mutex.release()
      print('Market operation already executed!')
      return
    self.done = True
    self.mutex.release()
    with requests.Session() as session:
      # buy <bcoin> immediately at market price, get qty. and avg. price
      qty, price = buy_coin_market(session, bcoin, self.qamount)
      # sell bought <bcoin> with <profit>% profit
      sell_coin_limit(session, bcoin, qty, price)


def coin_from_input(manager: MarketManager):
  bcoin = ''
  while bcoin == '':
    bcoin = input('Enter base coin symbol (coin to buy and sell): ').upper()
  manager.start(bcoin)


def coin_from_http(manager: MarketManager):
  acceptor = HTTPCoinAcceptor(manager, server_host, server_port)
  acceptor.start()


def main():
  global qcoin, profit

  iqcoin = input(f'Enter quote coin symbol (coin to trade for) [default: {qcoin}]: ')
  qcoin = qcoin if iqcoin == '' else iqcoin.upper()

  iprofit = input(f'Enter desired profit in % [default: {profit}]: ')
  profit = profit if iprofit == '' else float(iprofit)

  with requests.Session() as session:
    qbalance = coin_balance(session)
  qamount = input(f'Enter {qcoin} amount to sell [default: {qbalance}]: ')
  qamount = qbalance if qamount == '' else float(qamount)

  manager = MarketManager(qamount)

  input_thr = Thread(target=coin_from_input, args = (manager,))
  http_thr  = Thread(target=coin_from_http, args = (manager,))

  input_thr.start()
  http_thr.start()

  input_thr.join()
  http_thr.join()


env = get_env_data_as_dict(env_path)
if __name__ == '__main__':
  main()
