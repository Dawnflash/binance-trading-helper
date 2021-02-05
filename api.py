""" Binance API structures
"""

import requests
import hmac
import hashlib
import urllib
from util import Environment, bencode, ffmt, tstamp, InvalidPair


# class for communicating with Binance API
class BinanceApi:
  class ApiError(ValueError):
    def __init__(self, msg, data):
      self.data = data
      super().__init__(msg)

  def __init__(self, env: Environment):
    self.env  = env
    self.url  = env['BASE_API_URL']
    self.key  = env['BINANCE_API_KEY']
    self.bsec = bencode(env['BINANCE_API_SECRET'])
    self.pair = {} # current trading pair
    self.price_precision = 8

  # format base asset amount by Binance rules (use for base amounts)
  def bfmt(self, val: float) -> str:
    return ffmt(val, self.pair['baseAssetPrecision'])

  # format quote asset amount by Binance rules (use for prices)
  def qfmt(self, val: float) -> str:
    return ffmt(val, self.price_precision)

  # set current trading pair and price precision
  def set_pair(self, pair: dict):
    self.pair = pair
    qprec = pair['quoteAssetPrecision']
    tsize = int(float(pair['filters'][0]['tickSize']) * 10 ** qprec)
    self.price_precision = qprec - len(str(tsize)) + len(str(tsize).rstrip('0'))

  # sign <val> dictionary with SHA256 HMAC (pass your request parameters to <val>)
  def sign(self, val: dict) -> str:
    bval = bencode(urllib.parse.urlencode(val))
    return hmac.new(self.bsec, bval, hashlib.sha256).hexdigest()

  # make a Binance API request
  def req(self, session: requests.Session,
          method: str,
          uri: str,
          params: dict = {},
          headers: dict = {},
          signed: bool = True) -> (dict, dict):
    headers['Accept'] = 'application/json'

    if signed:
      headers['X-MBX-APIKEY'] = self.key
      params['signature'] = self.sign(params)

    url = urllib.parse.urljoin(self.url, uri)
    resp = session.request(method, url, params=params, headers=headers)

    # check for issues
    if resp.status_code == 418: # IP ban
      raise Exception(f"Your IP is banned for {resp.headers['Retry-After']} seconds. Wait!")
    if resp.status_code == 429: # Rate limiting
      raise Exception(f"Your IP is rate limited for {resp.headers['Retry-After']} seconds. Wait!")
    if not resp.ok: # other issues
      raise self.ApiError(f'Request for {url} failed\n{resp.status_code}: {resp.json()}', resp.json())
    return resp.headers, resp.json()

  # return exchange info
  def exchange_info(self, session: requests.Session) -> dict:
    _, resp = self.req(session, 'GET', 'exchangeInfo', signed=False)
    return resp

  # return 5min average price
  def avg_price(self, session: requests.Session) -> float:
    _, resp = self.req(session, 'GET', 'avgPrice',
                      {'symbol': self.pair['symbol']}, signed=False)
    return resp

  # return upper sell price limit
  def sell_max_limit(self, session: requests.Session) -> float:
    avg = self.avg_price(session)
    # base for the percent limit multiplier
    avgp = float(avg['price']) / avg['mins'] * self.pair['filters'][1]['avgPriceMins']
    plimit = avgp * float(self.pair['filters'][1]['multiplierUp'])
    # fixed limit
    flimit = float(self.pair['filters'][0]['maxPrice'])
    # take 98% of the upper percent limit just to be sure
    return min(0.98 * plimit, flimit)

  # return last price
  def last_price(self, session: requests.Session) -> float:
    _, resp = self.req(session, 'GET', 'ticker/price',
                      {'symbol': self.pair['symbol']}, signed=False)
    return float(resp['price'])

  # get <qcoin> balance in your spot account
  def coin_balance(self, session: requests.Session) -> float:
    _, resp = self.req(session, 'GET', 'account', {'timestamp': tstamp()})
    if 'balances' not in resp:
      raise Exception(f'Failed to query user data, bailing out')
    for balance in resp['balances']:
      if balance['asset'] == self.env.qcoin:
        bf, bl = float(balance['free']), float(balance['locked'])
        print(f'Your free balance for {self.env.qcoin} is {ffmt(bf)} ' + \
              f'(locked: {ffmt(bl)})')
        return bf
    raise Exception(f'Coin {self.env.qcoin} not found in your account. ' + \
                    'Make sure to choose a valid coin!')

  # get valid exchange symbols for a give quote coin
  def quote_symbols(self, exinfo: dict) -> dict:
    symbols = {}
    if 'symbols' not in exinfo:
      raise InvalidPair('Symbols not found in exchange info')
    for symbol in exinfo['symbols']:
      if symbol['status'] == 'TRADING' and \
        symbol['isSpotTradingAllowed'] and \
        symbol['quoteOrderQtyMarketAllowed'] and \
        symbol['quoteAsset'] == self.env.qcoin:
        symbols[symbol['baseAsset']] = symbol
    if not symbols:
      raise InvalidPair(f'No usable trading pairs found for quote asset {self.env.qcoin}')
    return symbols

  # print buy order status and return average fill price
  def order_status(self, resp: dict) -> (float, float):
    bcoin = self.pair['baseAsset']
    bqty, qqty = float(resp["executedQty"]), float(resp["cummulativeQuoteQty"])
    print(f'Executed market order (status: {resp["status"]})')
    v1 = 'bought' if resp['side'] == 'BUY' else 'sold'
    v2 = 'with' if resp['side'] == 'BUY' else 'for'
    print(f'{v1.capitalize()} {self.bfmt(bqty)} {bcoin} {v2} {self.qfmt(qqty)} {self.env.qcoin}')
    print('Fills:')
    price = 0
    for fill in resp['fills']:
      p, q = float(fill['price']), float(fill['qty'])
      price += p * q / bqty
      print(f'  {self.bfmt(q)} {bcoin} at {self.qfmt(p)} {self.env.qcoin} ' + \
            f'(fee: {fill["commission"]} {fill["commissionAsset"]})')
    print(f'Average price: {self.qfmt(price)} {self.env.qcoin}')
    if (price == 0):
      raise ValueError(f'Total price of {v1} {bcoin} seems to be zero. ' + \
                      'Something is wrong! Check Binance manually!')
    return bqty, price

  # buy <bcoin> with <qamount> of <qcoin> at market price
  # return amount of <bcoin> purchased (executed) and average (weighted) price in <qcoin>
  def buy_coin_market(self, session: requests.Session, qamount: float) -> (float, float):
    bcoin = self.pair['baseAsset']
    print(f'Buying {bcoin} using {self.qfmt(qamount)} {self.env.qcoin} at market price...')

    params = {
      'symbol': self.pair['symbol'],
      'side': 'BUY',
      'type': 'MARKET',
      'quoteOrderQty': self.qfmt(qamount), # buy with <qamount> of <qcoin>
      'timestamp': tstamp()
    }
    _, resp = self.req(session, 'POST', 'order', params)

    return self.order_status(resp)

  # sell <bamount> of <bcoin> for <qcoin> at market price
  # return amount of <qcoin> purchased (executed) and average (weighted) price in <bcoin>
  def sell_coin_market(self, session: requests.Session, bamount: float) -> (float, float):
    bcoin = self.pair['baseAsset']
    print(f'Selling {bcoin} using {self.qfmt(bamount)} {self.env.qcoin} at market price...')

    params = {
      'symbol': self.pair['symbol'],
      'side': 'SELL',
      'type': 'MARKET',
      'quantity': self.bfmt(bamount), # sell <bamount> of <bcoin>
      'timestamp': tstamp()
    }
    _, resp = self.req(session, 'POST', 'order', params)

    return self.order_status(resp)

  # sell <bamount> of <bcoin>, return success
  def sell_coin_limit(self, session: requests.Session, bamount: float, price: float) -> bool:
    bcoin = self.pair['baseAsset']
    print(f'Selling {self.bfmt(bamount)} {bcoin} for {self.env.qcoin} ' + \
          f'at price limit {self.qfmt(price)}...')

    params = {
      'symbol': self.pair['symbol'],
      'side': 'SELL',
      'type': 'LIMIT',
      'timeInForce': 'GTC', # good till cancelled
      'quantity': self.bfmt(bamount),
      'price': self.qfmt(price),
      'timestamp': tstamp()
    }
    try:
      _, resp = self.req(session, 'POST', 'order', params)
      print(f'Executed limit sell order (status: {resp["status"]})')
    except BinanceApi.ApiError as e:
      print(f'Limit sell failed with {e.data}')
      return False
    return True
