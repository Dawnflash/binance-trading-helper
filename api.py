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


base_url = 'https://api.binance.com/api/v3/' # base Binance API URL
env_path = '.env' # path to the environment file containing API keys
# defaults (may be overriden at runtime)
qcoin = 'BTC' # quote coin (what are we trading for, uppercase)
profit = 400 # [%] target profit (percentage of buy price)


# read file at <path> and extract envvars from it
def get_env_data_as_dict(path: str) -> dict:
  with open(path, 'r') as f:
    return dict(tuple(line.replace('\n', '').split('=')) for line
      in f.readlines() if not line.startswith('#'))


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
      print(f'Your free balance for {qcoin} is {balance["free"]} (locked: {balance["locked"]})')
      return float(balance['free'])
  raise Exception(f'Coin {qcoin} not found in your account. Make sure to choose a valid coin!')


# print buy order status and return average fill price
def order_status(bcoin: str, resp: dict) -> (float, float):
  print(f'Order status: {resp["status"]}')
  print(f'Bought {resp["executedQty"]} {bcoin} using {resp["cummulativeQuoteQty"]} {qcoin}')
  print('Fills:')
  price = 0
  for fill in resp['fills']:
    price += float(fill['price']) * float(fill['qty']) / float(resp['executedQty'])
    print(f'  {fill["qty"]} {bcoin} at {fill["price"]} {qcoin} (fee: {fill["commission"]} {fill["commissionAsset"]})')
  print(f'Average buy price: {price} {qcoin}')
  if (price == 0):
    raise Exception(f'Total price of bought {bcoin} seems to be zero. Something is wrong! Check Binance manually!')
  return float(resp['executedQty']), price


# buy <bcoin> with <qamount> of <qcoin> at market price
# return amount of <bcoin> purchased (executed) and average (weighted) price
def buy_coin_market(session: requests.Session, bcoin: str, qamount: float) -> (float, float):
  print(f'Buying {bcoin} with {qcoin} at market price...')

  params = {
    'symbol': bcoin + qcoin,
    'side': 'BUY',
    'type': 'MARKET',
    'quoteOrderQty': qamount, # buy with <qamount> of <qcoin>
    'timestamp': tstamp()
  }
  _, resp = api_req(session, 'POST', 'order', params)

  return order_status(bcoin, resp)


# sell <bamount> of <bcoin>
def sell_coin_limit(session: requests.Session, bcoin: str, bamount: float, price: float):
  limit = (100 + profit) / 100 * price
  print(f'Selling {bamount} {bcoin} for {qcoin} with {profit}% profit at price limit {limit}...')

  params = {
    'symbol': bcoin + qcoin,
    'side': 'SELL',
    'type': 'LIMIT',
    'timeInForce': 'GTC', # good till cancelled
    'quantity': bamount,
    'price': limit,
    'timestamp': tstamp()
  }
  _, resp = api_req(session, 'POST', 'order', params)
  print(f'Executed limit sell order (status: {resp["status"]})')
  print(f"Check the {bcoin}/{qcoin} trading pair on Binance now!")


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

  bcoin = ''
  while bcoin == '':
    bcoin = input('Enter base coin symbol (coin to buy and sell): ').upper()

  with requests.Session() as session:
    # buy <bcoin> immediately at market price, get qty. and avg. price
    qty, price = buy_coin_market(session, bcoin, qamount)
    # sell bought <bcoin> with <profit>% profit
    sell_coin_limit(session, bcoin, qty, price)


env = get_env_data_as_dict(env_path)
if __name__ == '__main__':
  main()
