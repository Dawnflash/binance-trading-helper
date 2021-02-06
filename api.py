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
    self.pfilt = {}
    self.price_tick = 8
    self.lot_tick = 8
    self.mkt_lot_tick = 8

  # format base asset amount by Binance rules (use for base amounts)
  def bfmt(self, val: float) -> str:
    return ffmt(val, self.lot_tick)

  # format base asset amount by Binance rules (for market orders)
  def bfmt_mkt(self, val: float) -> str:
    return ffmt(val, self.mkt_lot_tick)

  # format quote asset amount by Binance rules (use for prices)
  def qfmt(self, val: float) -> str:
    return ffmt(val, self.price_tick)

  # set current trading pair and price precision
  def set_pair(self, pair: dict):
    self.pair = pair
    for f in pair['filters']:
      self.pfilt[f['filterType']] = f

    qprec = pair['quoteAssetPrecision']
    bprec = pair['baseAssetPrecision']
    if 'PRICE_FILTER' in self.pfilt:
      tsize = int(float(self.pfilt['PRICE_FILTER']['tickSize']) * 10 ** qprec)
      if tsize:
        self.price_tick = qprec - len(str(tsize)) + len(str(tsize).rstrip('0'))
    if 'LOT_SIZE' in self.pfilt:
      tsize = int(float(self.pfilt['LOT_SIZE']['stepSize']) * 10 ** bprec)
      if tsize:
        self.lot_tick = bprec - len(str(tsize)) + len(str(tsize).rstrip('0'))
    if 'MARKET_LOT_SIZE' in self.pfilt:
      tsize = int(float(self.pfilt['MARKET_LOT_SIZE']['stepSize']) * 10 ** bprec)
      if tsize:
        self.mkt_lot_tick = bprec - len(str(tsize)) + len(str(tsize).rstrip('0'))
    else:
      self.mkt_lot_tick = self.lot_tick

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

  # return 1min average price
  def avg_price(self, session: requests.Session) -> float:
    _, resp = self.req(session, 'GET', 'avgPrice',
                      {'symbol': self.pair['symbol']}, signed=False)
    return float(resp['price'])

  # return lower and upper price limit (<avg> avg price)
  def price_bound(self, avg: float) -> (float, float):
    dnlimit, dnflimit = 0, 0
    uplimit, upflimit = float('inf'), float('inf')
    if 'PERCENT_PRICE' in self.pfilt:
      # relative limit
      dnlimit = avg * float(self.pfilt['PERCENT_PRICE']['multiplierDown'])
      uplimit = avg * float(self.pfilt['PERCENT_PRICE']['multiplierUp'])
    if 'PRICE_FILTER' in self.pfilt:
      # fixed limit
      dnflimit = float(self.pfilt['PRICE_FILTER']['minPrice'])
      upflimit = float(self.pfilt['PRICE_FILTER']['maxPrice'])
    # take 5% of relative limits just to be sure
    return max(1.05 * dnlimit, dnflimit), min(0.95 * uplimit, upflimit)

  # return lower and upper quantity limit
  # <price> limit price or avg price (for market orders)
  # <market> is market order
  def qty_bound(self, price, market: bool = False) -> (float, float):
    # get notional lower bound
    not_lim = 0
    if 'MIN_NOTIONAL' in self.pfilt:
      f = self.pfilt['MIN_NOTIONAL']
      m = float(f['minNotional'])
      if market and f['applyToMarket'] or not market:
        not_lim = m / price * 1.05 # add 5% just to be sure

    if market and 'MARKET_LOT_SIZE' in self.pfilt:
      f = self.pfilt['MARKET_LOT_SIZE']
      return max(float(f['minQty']), not_lim), float(f['maxQty'])
    if 'LOT_SIZE' in self.pfilt:
      f = self.pfilt['LOT_SIZE']
      return max(float(f['minQty']), not_lim), float(f['maxQty'])
    return 0, float('inf')

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

  # buy <bcoin> with <qqty> of <qcoin> at market price
  # return amount of <bcoin> purchased (executed) and average (weighted) price in <qcoin>
  def buy_coin_market(self, session: requests.Session, qqty: float) -> (float, float):
    bcoin = self.pair['baseAsset']
    print(f'Buying {bcoin} using {self.qfmt(qqty)} {self.env.qcoin} at market price...')

    params = {
      'symbol': self.pair['symbol'],
      'side': 'BUY',
      'type': 'MARKET',
      'quoteOrderQty': self.qfmt(qqty), # buy with <qqty> of <qcoin>
      'timestamp': tstamp()
    }
    _, resp = self.req(session, 'POST', 'order', params)

    return self.order_status(resp)

  # sell <bqty> of <bcoin> for <qcoin> at market price
  # return amount of <qcoin> purchased (executed) and average (weighted) price in <bcoin>
  def sell_coin_market(self, session: requests.Session, bqty: float) -> (float, float):
    bcoin = self.pair['baseAsset']
    print(f'Selling {self.bfmt_mkt(bqty)} {bcoin} at market price...')

    params = {
      'symbol': self.pair['symbol'],
      'side': 'SELL',
      'type': 'MARKET',
      'quantity': self.bfmt_mkt(bqty), # sell <bqty> of <bcoin>
      'timestamp': tstamp()
    }
    _, resp = self.req(session, 'POST', 'order', params)

    return self.order_status(resp)

  # sell <bqty> of <bcoin>, return success
  def sell_coin_limit(self, session: requests.Session, bqty: float, price: float) -> bool:
    bcoin = self.pair['baseAsset']
    print(f'Selling {self.bfmt(bqty)} {bcoin} for {self.env.qcoin} ' + \
          f'at price limit {self.qfmt(price)}...')

    params = {
      'symbol': self.pair['symbol'],
      'side': 'SELL',
      'type': 'LIMIT',
      'timeInForce': 'GTC', # good till cancelled
      'quantity': self.bfmt(bqty),
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
  
  # sell <bqty> of <bcoin> as OCO, return success
  def sell_coin_oco(self, session: requests.Session,
                    bqty: float, price: float, sprice: float) -> bool:
    bcoin = self.pair['baseAsset']
    print(f'Selling {self.bfmt(bqty)} {bcoin} for {self.env.qcoin} ' + \
          f'at price limit {self.qfmt(price)}...')

    params = {
      'symbol': self.pair['symbol'],
      'side': 'SELL',
      'quantity': self.bfmt(bqty),
      'price': self.qfmt(price),
      'stopPrice': self.qfmt(sprice),
      'stopLimitPrice': self.qfmt(sprice * 0.95),
      'stopLimitTimeInForce': 'GTC', # good till cancelled
      'timestamp': tstamp()
    }
    try:
      _, resp = self.req(session, 'POST', 'order/oco', params)
      print(f'Executed OCO order (status: {resp["listOrderStatus"]})')
    except BinanceApi.ApiError as e:
      print(f'OCO sell failed with {e.data}')
      return False
    return True
