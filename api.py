""" Binance API logic
"""

import json
import hmac
import hashlib
import urllib
import aiohttp
import websockets
from util import Environment, bencode, ffmt, tstamp, InvalidPair, CColors, CException


class BinanceAPI:
    """ class for communicating with Binance API """
    class ApiError(CException):
        """ Unexpected but possibly non-critical API response """
        def __init__(self, msg, data):
            self.data = data
            super().__init__(msg)
    class TickSizes:
        """ Tick sizes for various Binance use cases """
        def __init__(self):
            self.price = 8
            self.lot = 8
            self.mkt_lot = 8

    # initialize API from environment
    # run `set_pair` before using trading methods
    def __init__(self, env: Environment):
        self.env  = env
        self.url  = env['BASE_API_URL']
        self.key  = env['BINANCE_API_KEY']
        self.bsec = bencode(env['BINANCE_API_SECRET'])
        self.pair = {} # current trading pair
        self.pfilt = {} # pair filters
        # ticks for lots and prices
        self.ticks = self.TickSizes()

    def bfmt(self, val: float) -> str:
        """ format base asset amount by Binance rules (use for base amounts) """
        return ffmt(val, self.ticks.lot)

    def bfmt_mkt(self, val: float) -> str:
        """ format base asset amount by Binance rules (for market orders) """
        return ffmt(val, self.ticks.mkt_lot)

    def qfmt(self, val: float) -> str:
        """ format quote asset amount by Binance rules (use for prices) """
        return ffmt(val, self.ticks.price)

    def set_pair(self, pair: dict):
        """ set current trading pair, its filters and tick sizes
            calling this method is required to use trading features """
        self.pair = pair
        for filt in pair['filters']:
            self.pfilt[filt['filterType']] = filt

        qprec = pair['quoteAssetPrecision']
        bprec = pair['baseAssetPrecision']
        if 'PRICE_FILTER' in self.pfilt:
            tsize = int(float(self.pfilt['PRICE_FILTER']['tickSize']) * 10 ** qprec)
            if tsize:
                self.ticks.price = qprec - len(str(tsize)) + len(str(tsize).rstrip('0'))
        if 'LOT_SIZE' in self.pfilt:
            tsize = int(float(self.pfilt['LOT_SIZE']['stepSize']) * 10 ** bprec)
            if tsize:
                self.ticks.lot = bprec - len(str(tsize)) + len(str(tsize).rstrip('0'))
        if 'MARKET_LOT_SIZE' in self.pfilt:
            tsize = int(float(self.pfilt['MARKET_LOT_SIZE']['stepSize']) * 10 ** bprec)
            if tsize:
                self.ticks.mkt_lot = bprec - len(str(tsize)) + len(str(tsize).rstrip('0'))
        else:
            self.ticks.mkt_lot = self.ticks.lot

    def sign(self, val: dict) -> str:
        """ sign <val> dictionary with SHA256 HMAC (pass your request parameters to <val>) """
        bval = bencode(urllib.parse.urlencode(val))
        return hmac.new(self.bsec, bval, hashlib.sha256).hexdigest()

    def price_bound(self, avg: float) -> (float, float):
        """ return lower and upper price limit (<avg> average trading price) """
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

    def qty_bound(self, price, market: bool = False) -> (float, float):
        """ return lower and upper quantity limit
            <price> limit price or avg price (for market orders)
            <market> is market order """
        # get notional lower bound
        not_lim = 0
        if 'MIN_NOTIONAL' in self.pfilt:
            filt = self.pfilt['MIN_NOTIONAL']
            lim = float(filt['minNotional'])
            if market and filt['applyToMarket'] or not market:
                not_lim = lim / price * 1.05 # add 5% just to be sure

        if market and 'MARKET_LOT_SIZE' in self.pfilt:
            filt = self.pfilt['MARKET_LOT_SIZE']
            return max(float(filt['minQty']), not_lim), float(filt['maxQty'])
        if 'LOT_SIZE' in self.pfilt:
            filt = self.pfilt['LOT_SIZE']
            return max(float(filt['minQty']), not_lim), float(filt['maxQty'])
        return 0, float('inf')

    def quote_symbols(self, exinfo: dict) -> (dict, dict, dict):
        """ get valid trade symbols for a given quote coin in a dictionary,
            symbols quoted in source coins and based on the quote coin
            and a single symbol quoted by BUSD or USDT """
        symbols, src_symbols, usd_symbol = {}, {}, {}
        if 'symbols' not in exinfo:
            raise InvalidPair('Symbols not found in exchange info')
        for symbol in exinfo['symbols']:
            if symbol['status'] == 'TRADING' and \
                symbol['isSpotTradingAllowed'] and \
                symbol['quoteOrderQtyMarketAllowed']:
                if symbol['quoteAsset'] == self.env.qcoin:
                    symbols[symbol['baseAsset']] = symbol
                    continue
                if not usd_symbol and \
                   symbol['baseAsset'] == self.env.qcoin and \
                   symbol['quoteAsset'] in ('BUSD', 'USDT'):
                    usd_symbol = symbol
                    continue
                if symbol['quoteAsset'] in self.env.src_coins and \
                   symbol['baseAsset'] == self.env.qcoin:
                    src_symbols[symbol['quoteAsset']] = symbol
        if not symbols:
            raise InvalidPair(f'No usable */{self.env.qcoin} trading pairs found')
        return symbols, src_symbols, usd_symbol

    def market_order_status(self, resp: dict) -> (float, float):
        """ print market order status and return executed qty and average fill price
            <resp>: response object from `req` """
        bcoin, qcoin = self.pair['baseAsset'], self.pair['quoteAsset']
        bqty, qqty = float(resp["executedQty"]), float(resp["cummulativeQuoteQty"])
        CColors.iprint(f'Executed market order (status: {resp["status"]})')
        if resp['side'] == 'BUY':
            print(f'Bought {self.bfmt(bqty)} {bcoin} with {self.qfmt(qqty)} {qcoin}')
        else:
            print(f'Sold {self.bfmt(bqty)} {bcoin} for {self.qfmt(qqty)} {qcoin}')
        print('Fills:')
        avg_price = 0
        for fill in resp['fills']:
            price, qty = float(fill['price']), float(fill['qty'])
            avg_price += price * qty / bqty
            print(f'  {self.bfmt(qty)} {bcoin} at {self.qfmt(price)} {qcoin} ' + \
                        f'(fee: {fill["commission"]} {fill["commissionAsset"]})')
        print(f'Average fill price: {self.qfmt(avg_price)} {qcoin}')
        if not avg_price:
            raise CException('Average fill price seems to be zero')
        return bqty, avg_price

    async def req(self, client: aiohttp.ClientSession,
                  method: str,
                  uri: str,
                  params: dict,
                  headers: dict,
                  signed: bool = True) -> (dict, dict):
        """ make a Binance API request, return a parsed JSON response """
        headers['Accept'] = 'application/json'

        if signed:
            headers['X-MBX-APIKEY'] = self.key
            params['timestamp'] = tstamp()
            params['signature'] = self.sign(params)

        url = urllib.parse.urljoin(self.url, uri)
        resp = await client.request(method, url, params=params, headers=headers)

        # check for issues
        if resp.status == 418: # IP ban
            raise CException(f"Your IP is banned for {resp.headers['Retry-After']} seconds. Wait!")
        if resp.status == 429: # Rate limiting
            raise CException(f"Your IP is rate limited for ' + \
                             '{resp.headers['Retry-After']} seconds. Wait!")

        jresp = await resp.json()
        if not resp.ok: # other issues
            raise self.ApiError(f'Request for {url} failed\n{resp.status}: {jresp}', jresp)
        return resp.headers, jresp

    async def exchange_info(self, client: aiohttp.ClientSession) -> dict:
        """ return exchange info """
        _, resp = await self.req(client, 'GET', 'exchangeInfo', {}, {}, False)
        return resp

    async def avg_price(self, client: aiohttp.ClientSession) -> float:
        """ return the average trading price """
        _, resp = await self.req(client, 'GET', 'avgPrice',
                                 {'symbol': self.pair['symbol']}, {}, False)
        return float(resp['price'])

    async def last_price(self, client: aiohttp.ClientSession) -> float:
        """ return last traded price """
        _, resp = await self.req(client, 'GET', 'ticker/price',
                                 {'symbol': self.pair['symbol']}, {}, False)
        return float(resp['price'])

    async def balances(self, client: aiohttp.ClientSession) -> float:
        """ return coin balances """
        _, resp = await self.req(client, 'GET', 'account', {}, {})
        if 'balances' not in resp:
            raise CException('Failed to query user data, bailing out')
        return resp['balances']

    async def buy_coin_market(self, client: aiohttp.ClientSession,
                              qqty: float) -> (bool, float, float):
        """ buy <bcoin> with <qqty> of <qcoin> at market price
            return amount of <bcoin> purchased and average trade price """
        bcoin, qcoin = self.pair['baseAsset'], self.pair['quoteAsset']
        msg = f'[MARKET BUY] Buying {bcoin} with {self.qfmt(qqty)} {qcoin}'
        CColors.cprint(msg, CColors.WARNING)

        params = {
            'symbol': self.pair['symbol'],
            'side': 'BUY',
            'type': 'MARKET',
            'quoteOrderQty': self.qfmt(qqty), # buy with <qqty> of <qcoin>
        }
        try:
            _, resp = await self.req(client, 'POST', 'order', params, {})
            return (True, *self.market_order_status(resp))
        except BinanceAPI.ApiError as exc:
            CColors.eprint(f'Market buy failed with {exc.data}')
            return False, 0, 0

    async def sell_coin_market(self, client: aiohttp.ClientSession,
                               bqty: float) -> (bool, float, float):
        """ sell <bqty> of <bcoin> for <qcoin> at market price
            return success, amount of <qcoin> purchased and average trade price """
        bcoin = self.pair['baseAsset']
        CColors.cprint(f'[MARKET SELL] Selling {self.bfmt_mkt(bqty)} {bcoin}',
                       CColors.WARNING)

        params = {
            'symbol': self.pair['symbol'],
            'side': 'SELL',
            'type': 'MARKET',
            'quantity': self.bfmt_mkt(bqty), # sell <bqty> of <bcoin>
        }
        try:
            _, resp = await self.req(client, 'POST', 'order', params, {})
            return (True, *self.market_order_status(resp))
        except BinanceAPI.ApiError as exc:
            CColors.eprint(f'Market sell failed with {exc.data}')
            return False, 0, 0

    async def sell_coin_limit(self, client: aiohttp.ClientSession,
                              bqty: float, price: float) -> int:
        """ sell <bqty> of <bcoin>, return order ID (0 = fail) """
        bcoin = self.pair['baseAsset']
        CColors.cprint(f'[LIMIT SELL] Selling {self.bfmt(bqty)} {bcoin} at {self.qfmt(price)}',
                       CColors.WARNING)

        params = {
            'symbol': self.pair['symbol'],
            'side': 'SELL',
            'type': 'LIMIT',
            'timeInForce': 'GTC', # good till cancelled
            'quantity': self.bfmt(bqty),
            'price': self.qfmt(price),
        }
        try:
            _, resp = await self.req(client, 'POST', 'order', params, {})
            CColors.iprint(f'Executed limit sell order (status: {resp["status"]})')
        except BinanceAPI.ApiError as exc:
            CColors.eprint(f'Limit sell failed with {exc.data}')
            return 0
        return resp['orderId']

    async def sell_coin_oco(self, client: aiohttp.ClientSession,
                            bqty: float, price: float, sprice: float) -> int:
        """ sell <bqty> of <bcoin> as OCO, return order ID (0 = fail) """
        bcoin = self.pair['baseAsset']
        msg = f'[OCO SELL] Selling {self.bfmt(bqty)} {bcoin} at {self.qfmt(price)}' + \
              f', stop: {self.qfmt(sprice)}'
        CColors.cprint(msg, CColors.WARNING)

        params = {
            'symbol': self.pair['symbol'],
            'side': 'SELL',
            'quantity': self.bfmt(bqty),
            'price': self.qfmt(price),
            'stopPrice': self.qfmt(sprice),
            'stopLimitPrice': self.qfmt(sprice * 0.95),
            'stopLimitTimeInForce': 'GTC' # good till cancelled
        }
        try:
            _, resp = await self.req(client, 'POST', 'order/oco', params, {})
            CColors.iprint(f'Executed OCO order (status: {resp["listOrderStatus"]})')
        except BinanceAPI.ApiError as exc:
            CColors.eprint(f'OCO sell failed with {exc.data}')
            return 0
        return resp['orderListId']

    async def cancel_order(self, client: aiohttp.ClientSession, order_id: int) -> (bool, float):
        """ Cancel a regular order, return success and executed quantity """
        params = {
            'symbol': self.pair['symbol'],
            'orderId': order_id
        }
        try:
            _, resp = await self.req(client, 'DELETE', 'order', params, {})
            CColors.iprint(f'Canceled limit order #{order_id} (status: {resp["status"]})')
            return True, float(resp['executedQty'])
        except BinanceAPI.ApiError as exc:
            CColors.eprint(f'Order cancel failed with {exc.data}')
            return False, 0

    async def cancel_oco_order(self, client: aiohttp.ClientSession,
                               order_list_id: int) -> (bool, float):
        """ Cancel an OCO order, return success and executed quantity """
        params = {
            'symbol': self.pair['symbol'],
            'orderListId': order_list_id
        }
        try:
            _, resp = await self.req(client, 'DELETE', 'orderList', params, {})
            CColors.iprint(f'Canceled OCO #{order_list_id} (status: {resp["listOrderStatus"]})')
            qty = sum((float(rep['executedQty']) for rep in resp['orderReports']))
            return True, qty
        except BinanceAPI.ApiError as exc:
            CColors.eprint(f'OCO order cancel failed with {exc.data}')
            return False, 0

class BinanceWSAPI:
    """ class for communicating with Binance WebSockets API """
    def __init__(self, env: Environment):
        self.url = env['BASE_WSAPI_URL']

    async def read_single(self, uri: str):
        """ subscribe to a single stream and yield data on reception """
        url = urllib.parse.urljoin(self.url, uri)
        async with websockets.connect(url, ssl=True) as wsock:
            while True:
                yield json.loads(await wsock.recv())

    async def agg_trades(self, symbol: str):
        """ get aggregated trade data for a given symbol """
        async for val in self.read_single(f'ws/{symbol.lower()}@aggTrade'):
            yield val

    async def tickers(self, symbol: str):
        """ get 24h ticker data for a given symbol """
        async for val in self.read_single(f'ws/{symbol.lower()}@ticker'):
            yield val
