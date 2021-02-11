#!/usr/bin/env python
""" This module monitors the market for favorable changes
"""

import asyncio
from datetime import datetime
import aiohttp
from api import BinanceAPI, BinanceWSAPI
from util import Environment, CColors, CException

class MinuteKline:
    """ represents a 1m Binance kline """
    def __init__(self, data, ws: bool = True):
        if ws:
            self.update_ws(data)
        else:
            self.update_rest(data)

    def update_ws(self, data: dict):
        """ Set kline from WS API data """
        self.open     = data['t']
        self.ntrades  = data['n']
        self.price_cl = float(data['c'])
        self.price_lo = float(data['l'])
        self.price_hi = float(data['h'])
        self.qvol     = float(data['q'])
        self.qvol_buy = float(data['Q'])

    def update_rest(self, data: list):
        """ Set kline from REST API data """
        self.open     = data[0]
        self.ntrades  = data[8]
        self.price_cl = float(data[4])
        self.price_lo = float(data[3])
        self.price_hi = float(data[2])
        self.qvol     = float(data[7])
        self.qvol_buy = float(data[10])

class KlineStorage:
    """ storage of klines for a single symbol """
    def __init__(self, symbol: dict, thresh: float = 0.1):
        self.symbol = symbol
        self.thresh = thresh
        self.nalarm = {}
        self.klines = []

    def prefix(self, mult: int):
        """ printing prefix """
        ticker  = self.symbol['ticker']
        stime   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ssym    = f'{self.symbol["baseAsset"]}/{self.symbol["quoteAsset"]}'
        sstats  = f'24h vol.: {int(float(ticker["quoteVolume"]))} {self.symbol["quoteAsset"]}, ' + \
                  f'chg.: {ticker["priceChangePercent"]}%'
        return f'[{stime}] {ssym} {mult}x ({sstats}):'

    def analyze(self):
        """ analyze single symbol """
        # 5-min analysis
        self.analyze_mins(5, CColors.WARNING)
        self.analyze_mins(10, CColors.OKCYAN)
        self.analyze_mins(20, CColors.OKBLUE)
        self.analyze_mins(30, CColors.OKGREEN)

    def analyze_mins(self, mins: int, color: CColors):
        """ analyze symbol per minutes """
        klast = self.klines[-1]
        kprev = self.klines[-1 - mins]
        dcl = (klast.price_cl / kprev.price_cl - 1) * 100
        rvol = self.vol_ratio(mins)

        if mins not in self.nalarm:
            self.nalarm[mins] = 0

        if dcl > self.thresh:
            mult = self.nalarm[mins] + 1
            CColors.cprint(f'{self.prefix(mult)} up {dcl:.2f}% in {mins}min, vol. chg. {rvol:.2f}%',
                           color)
        else:
            mult = 0
        self.nalarm[mins] = mult

    def vol_ratio(self, mins: int) -> float:
        """ get volume ratios in % between first and last half of the interval """
        sep = -mins // 2
        lat = sum((self.klines[i].qvol for i in range(-1, sep, -1)))
        pre = sum((self.klines[i].qvol for i in range(sep, -mins - 1, -1))) + 1
        return (lat / pre - 1) * 100

class KlineManager:
    """ Manages received 1min Klines """

    def __init__(self, symbols: list, nklines: int, thresh: float):
        if nklines <= 0:
            raise CException(f'Bad kline count: {nklines}')
        self.nklines = nklines
        self.symbols = {}
        for symb in symbols:
            self.symbols[symb['symbol']] = KlineStorage(symb, thresh)

    def update(self, data: dict):
        """ receive a kline and use it """
        kstor = self.symbols[data['s']]
        if not kstor.klines or kstor.klines[-1].open < data['t']:
            if kstor.klines:
                kstor.klines.pop(0)
            kstor.klines.append(MinuteKline(data))
        else:
            kstor.klines[-1].update_ws(data)
        # run analysis if closed
        if data['x']:
            kstor.analyze()

    def fill(self, hdata: zip):
        """ fill symbols with historical data in format [(symbol, klines)] """
        for sname, klines in hdata:
            kstor = self.symbols[sname]
            for kdata in klines:
                kstor.klines.append(MinuteKline(kdata, False))

async def quote_symbols(api: BinanceAPI):
    """ Get all */quote and quote/BUSD symbols """
    async with aiohttp.ClientSession() as client:
        exinfo, tickers = await asyncio.gather(api.exchange_info(client),
                                               api.ticker_24h(client, single=False))
    dtickers = {val['symbol']: val for val in tickers}
    qsymbols = {}
    for symbol in exinfo['symbols']:
        if symbol['status'] != 'TRADING' or \
           not symbol['isSpotTradingAllowed'] or \
           not symbol['quoteOrderQtyMarketAllowed']:
            continue
        # inject ticker data inside a symbol
        symbol['ticker'] = dtickers[symbol['symbol']]
        if symbol['quoteAsset'] == api.env.qcoin:
            qsymbols[symbol['baseAsset']] = symbol
        elif symbol['quoteAsset'] == 'BUSD' and \
             symbol['baseAsset'] == api.env.qcoin:
            qsymbols[api.env.qcoin] = symbol
    return qsymbols

async def main():
    """ Entrypoint """
    klen = 241 # 4h remanence
    thresh = 5 # 5% sensitivity

    env  = Environment('.env')
    api  = BinanceAPI(env)
    wapi = BinanceWSAPI(env)
    # fetch symbols to track
    qsymbols = await quote_symbols(api)
    qvalues  = qsymbols.values()
    symb_names = [symb['symbol'] for symb in qvalues]
    qlen = len(qvalues)
    CColors.iprint(f'DawnSpotter online.\nTracking {qlen} pairs: {", ".join(symb_names)}')
    # prepare the kline data structure
    manager = KlineManager(qvalues, klen, thresh)
    # Pull historical data from the API
    maxrun = 1200 // (qlen + 41)
    print(f'Pulling historical data from REST API, do not rerun this more than {maxrun}x/min!')
    async with aiohttp.ClientSession() as client:
        coros = (api.last_klines(client, '1m', klen, symbol) for symbol in qvalues)
        preconf = await asyncio.gather(*coros)
    manager.fill(zip(symb_names, preconf))
    # read trade data from WS
    print('Updating data from WebSockets...')
    async for tdata in wapi.klines_bulk(symb_names, '1m'):
        manager.update(tdata['data']['k'])
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
