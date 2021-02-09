#!/usr/bin/env python

""" Binance quick market buy+limit sell script
Author: Dawnflash

Ensure .env is created (use .env.example as a template)
Follow the prompts and good luck.
"""

import asyncio
from threading import Thread, Lock, Condition
import aiohttp
from server import HTTPCoinAcceptor
from util import InvalidPair, Environment, SellType, CColors, CException, ffmt
from api import BinanceAPI, BinanceWSAPI


class MarketManager:
    """ Primary logic structure, manages trades """
    def __init__(self, api: BinanceAPI, wapi: BinanceWSAPI, pairs: dict, qqty: float):
        self.api = api
        self.wapi = wapi
        self.env = self.api.env
        # all possible */<quote coin> pairs
        self.pairs = pairs
        # special flags
        self.allow_bailout = False
        self.use_oco = False
        # pre-buy data
        self.qqty = qqty
        # after-buy data
        self.bqty   = 0 # total bought qty
        self.bprice = 0 # mean buy price
        self.tprice = 0 # target sell price
        self.sprice = 0 # stop price
        self.oid    = 0 # last order id
        # coin locking
        self.mut1 = Lock()
        self.locked = False
        # worker notification
        self.mut2 = Lock()
        self.cvar = Condition(self.mut2)
        self.ready = False

    def lock(self, bcoin: str):
        """ lock in a trade pair and notify main thread
            InvalidPair => bad pair/try again, Exception => abort
            on success notify a waiting thread to call `start` """
        self.mut1.acquire()
        # reject if locked
        if self.locked:
            self.mut1.release()
            raise CException('Market operation is already running!')
        # retry if bad coin is turned in
        if bcoin not in self.pairs:
            self.mut1.release()
            raise InvalidPair(f'Trading pair {bcoin}/{self.env.qcoin} not found')
        self.locked = True
        self.mut1.release()
        # lock in a trading pair
        self.api.set_pair(self.pairs[bcoin])
        self.use_oco = self.env.stop > -100 and self.api.pair['ocoAllowed']
        if self.env.stop > -100 and not self.api.pair['ocoAllowed']:
            CColors.wprint('You set a stop price but this trading pair doesn\'t allow OCO trades!')
        # notify worker
        with self.cvar:
            self.ready = True
            self.cvar.notify()

    async def start(self):
        """ start trading, only call this method after locking in a pair """
        CColors.iprint(f'Market manager started with pair {self.api.pair["symbol"]}')
        async with aiohttp.ClientSession() as client:
            # buy <bcoin> immediately at market price, get qty. and avg. price
            succ, self.bqty, self.bprice = await self.api.buy_coin_market(client, self.qqty)
            if not succ:
                return
            self.tprice = (1 + self.env.profit / 100) * self.bprice
            self.sprice = (1 + self.env.stop / 100) * self.bprice
            # check sell amount eligibility at this point
            await self.check_sell_eligibility(client)
            # sell bought <bcoin> with <profit>% profit
            await self.sell_coins(client)

    async def check_sell_eligibility(self, client: aiohttp.ClientSession):
        """ check if you can sell with your strategy """
        avg = await self.api.avg_price(client)
        if self.env.sell_type == SellType.MARKET:
            low, high = self.api.qty_bound(avg, True)
        else:
            # adjust profit/loss targets
            plow, phigh = self.api.price_bound(avg)
            self.tprice = min(self.tprice, phigh)
            low, high = self.api.qty_bound(self.tprice)
            if self.use_oco:
                self.sprice = max(self.sprice, plow)
                low, _  = self.api.qty_bound(self.sprice)

        if not low <= self.bqty <= high:
            raise CException('Sell quantity out of allowed bounds, cannot sell!')
        if not low * 1.1 <= self.bqty <= high * 0.9:
            CColors.wprint('Caution, you are nearing Binance\'s quantity limits, ' + \
                            'high price fluctuations might prohibit your sell!')

    def sell_market_report(self, sprice: float):
        """ report on a successful market sell
            <sprice>: average sell price """
        profit = 100 * (sprice / self.bprice - 1)
        if profit >= 0:
            CColors.cprint(f'[MARKET SELL PROFIT] +{profit:.2f}%', CColors.OKGREEN)
        else:
            CColors.cprint(f'[MARKET SELL LOSS] {profit:.2f}%', CColors.FAIL)

    async def sell_coins_market(self, client: aiohttp.ClientSession):
        """ sell coins using market sell """
        succ, _, price = await self.api.sell_coin_market(client, self.bqty)
        if not succ:
            raise CException('Market sell failed')
        self.sell_market_report(price)

    async def sell_coins_limit(self, client: aiohttp.ClientSession) -> int:
        """ sell coins using a limit or OCO sell, return order ID """
        # try OCO if suitable
        if self.use_oco:
            oid = await self.api.sell_coin_oco(client, self.bqty, self.tprice, self.sprice)
            if not oid:
                raise CException('OCO sell failed')
            CColors.cprint(f'[OCO SELL] target profit: {self.env.profit:.2f}%, ' + \
                           f'max loss: {-self.env.stop:.2f}%', CColors.OKGREEN)
            return oid
        # try limit sell
        oid = await self.api.sell_coin_limit(client, self.bqty, self.tprice)
        if not oid:
            raise CException('Limit sell failed')
        CColors.cprint(f'[LIMIT SELL] target profit: {self.env.profit:.2f}%', CColors.OKGREEN)
        return oid

    async def cancel_limit(self, client: aiohttp.ClientSession, oid: int):
        """ attempts to cancel a limit order and subtract its fills """
        if self.use_oco:
            succ, qty = await self.api.cancel_oco_order(client, oid)
        else:
            succ, qty = await self.api.cancel_order(client, oid)
        if not succ:
            raise CException('Unable to cancel limit/OCO order, ' + \
                             'it is probably executed already!')
        self.bqty -= qty

    async def sell_coins(self, client: aiohttp.ClientSession):
        """ coin selling logic """
        bcoin   = self.api.pair['baseAsset']
        lprice  = 0 # last traded price

        if self.env.sell_type == SellType.LIMIT:
            # put a limit order on the book immediately
            self.oid = await self.sell_coins_limit(client)
            if not self.env.bailout:
                return
        self.allow_bailout = self.env.bailout
        async for tdata in self.wapi.agg_trades(self.api.pair['symbol']):
            # last traded price is the current market price
            _lprice = float(tdata['p'])
            if _lprice == lprice:
                continue
            lprice = _lprice

            # calculate estimated profit
            eprofit = 100 * (lprice / self.bprice - 1)
            if eprofit >= 0:
                CColors.cprint(f'[+{eprofit:.2f}%] 1 {bcoin} = ' + \
                                f'{self.api.qfmt(lprice)} {self.env.qcoin}', CColors.OKGREEN)
            else:
                CColors.cprint(f'[{eprofit:.2f}%] 1 {bcoin} = ' + \
                                f'{self.api.qfmt(lprice)} {self.env.qcoin}', CColors.FAIL)

            if self.env.sell_type == SellType.LIMIT:
                # limit orders are here just to be able to bailout
                continue

            # sell if forced or profit/loss limits are triggered
            if lprice > self.tprice or lprice < self.sprice:
                self.allow_bailout = False
                await self.sell_coins_market(client)
                return

    async def bailout(self):
        """ bailout and immediately cancel current order and sell coins """
        if not self.allow_bailout:
            return
        async with aiohttp.ClientSession() as client:
            # Cancel limit orders if any
            if self.oid:
                await self.cancel_limit(client, self.oid)
            # Sell everything on market immediately
            CColors.wprint('Selling on market immediately!')
            succ, _, price = await self.api.sell_coin_market(client, self.bqty)
            if succ:
                self.sell_market_report(price)

def coin_from_stdin(manager: MarketManager):
    """ fetch a coin name from stdin """
    prompt = CColors.cstr('Enter base coin symbol (coin to buy and sell): ',
                          CColors.WARNING)
    while True:
        try:
            bcoin = input(prompt).upper()
        except Exception:
            break
        if not bcoin:
            continue
        try:
            manager.lock(bcoin)
            return
        except InvalidPair as exc:
            print(str(exc))
        except Exception as exc:
            print(str(exc))
            break
    with manager.cvar:
        manager.cvar.notify()

def coin_from_http(manager: MarketManager):
    """ start an HTTP server to listen for a coin name """
    acceptor = HTTPCoinAcceptor(manager, manager.env.conn)
    acceptor.start()

async def quote_qty_from_usd(client: aiohttp.ClientSession,
                             api: BinanceAPI, usd_symbol: dict) -> float:
    """ get quote quantity by its value in USD
        <usd_symbol>: a <quote>/BUSD or <quote>/USDT symbol """
    if api.env.usd_value < 0:
        raise CException('USD quote value cannot be negative')
    if usd_symbol == {}:
        raise CException('No BUSD/USDT trade symbol found, aborting!')
    api.set_pair(usd_symbol)
    return await api.last_price(client)

async def buy_from_source(client: aiohttp.ClientSession, api: BinanceAPI,
                          src_symbols: dict, balances: dict, bqty: float):
    """ Buy <val> of quote coin """
    for asset, (free, _) in balances.items():
        if free == 0 or asset not in src_symbols:
            continue
        symbol = src_symbols[asset]
        api.set_pair(symbol)
        avg, last = await asyncio.gather(api.avg_price(client),
                                         api.last_price(client))
        qqty = bqty * last
        if free < qqty:
            continue
        low, high = api.qty_bound(avg, True)
        bqty = max(low, bqty)
        qqty = bqty * last
        if free < qqty or bqty > high:
            continue
        succ, qty, _ = await api.buy_coin_market(client, qqty)
        if not succ:
            continue
        return qty
    raise CException('Failed to buy quote coin')

def filter_balances(balances: list, coins: list) -> (float, float):
    """ filter balances into a dictionary with listed coins and their balances """
    return {bal['asset']: (float(bal['free']), float(bal['locked']))
                for bal in balances if bal['asset'] in coins}


async def setup(api: BinanceAPI) -> (dict, float):
    """ main parameter setup
        return exchange info and quote amount to sell """
    env = api.env
    def set_stdin(prompt: str, default):
        if not env.override:
            return default
        ret = input(prompt)
        return ret if ret else default

    prompt = f'Enter quote coin symbol (coin to trade for) [default: {env.qcoin}]: '
    env.qcoin = set_stdin(prompt, env.qcoin).upper()

    async with aiohttp.ClientSession() as client:
        info, lbals = await asyncio.gather(api.exchange_info(client),
                                           api.balances(client))
        symbols, src_symbols, usd_symbol = api.quote_symbols(info)
        bals = filter_balances(lbals, [env.qcoin] + env.src_coins)
        if env.qcoin not in bals:
            raise CException('Quote coin is invalid')
        qbal, qloc = bals[env.qcoin]
        del bals[env.qcoin]
        print(f'Your free balance for {env.qcoin} is {ffmt(qbal)} (locked: {ffmt(qloc)})')
        def_qty = env.buy_perc / 100 * qbal
        if env.usd_value:
            # fixed USD quote balance feature
            usd_price = await quote_qty_from_usd(client, api, usd_symbol)
            qqty = env.usd_value / usd_price
            while qqty > qbal:
                diff = 1.02 * (qqty - qbal)
                qbal += await buy_from_source(client, api, src_symbols, bals, diff)
        else:
            prompt = f'Enter {env.qcoin} amount to sell ' + \
                     f'[default: {ffmt(def_qty)} ({env.buy_perc:.2f}%)]: '
            qqty = float(set_stdin(prompt, def_qty))
            if qqty <= 0:
                raise CException(f'Cannot sell non-positive amount of {env.qcoin}')
            if qqty > qbal:
                raise CException('Insufficient quote balance')

    prompt = f'Enter sell type (LIMIT|MARKET) [default: {env.sell_type.name}]: '
    env.sell_type = SellType(set_stdin(prompt, env.sell_type))

    prompt = f'Enter desired profit in % [default: {env.profit:.2f}]: '
    env.profit = float(set_stdin(prompt, env.profit))

    if env.profit <= 0:
        CColors.wprint('You have set a non-positive profit. Proceeding may net you a loss!')

    prompt = f'Enter stop level in % to manage risk [default: {env.stop:.2f}]: '
    env.stop = float(set_stdin(prompt, env.stop))
    if not -100 <= env.stop < env.profit:
        raise CException('Stop percentage must be lower than profits!')

    print('---- SELECTED OPTIONS ----')
    print(f'Selected quote coin: {env.qcoin}')
    print(f'Selected quote amount to sell: {ffmt(qqty)} {env.qcoin} ' + \
          f'(available: {ffmt(qbal)} {env.qcoin})')
    print(f'Selected sell strategy: {env.sell_type.name}')
    print(f'Selected target profit: {env.profit:.2f}%')
    print(f'Selected stop percentage: {env.stop:.2f}%')
    print('--------------------------')
    return symbols, qqty

def main():
    """ Entrypoint """
    env  = Environment('.env')
    api  = BinanceAPI(env)
    wapi = BinanceWSAPI(env)

    if env.override:
        print('Want to skip prompts? Set PROMPT_OVERRIDE to 0!')

    loop = asyncio.get_event_loop()
    # initialize Market Manager (prepare everything)
    manager = MarketManager(api, wapi, *loop.run_until_complete(setup(api)))

    # Start coin name listeners: stdin and HTTP
    http_thr  = Thread(target=coin_from_http, args=(manager,), daemon=True)
    stdin_thr = Thread(target=coin_from_stdin, args=(manager,), daemon=True)
    print(f'Starting HTTP listener at {env["SERVER_HOST"]}:{env["SERVER_PORT"]}')
    http_thr.start()
    stdin_thr.start()

    if env.bailout:
        print('Bailout enabled: once trading starts, press Ctrl+C to sell immediately')

    # wait for coin lock
    try:
        with manager.cvar:
            manager.cvar.wait_for(lambda: manager.ready)
    except KeyboardInterrupt:
        return

    # start trading
    try:
        loop.run_until_complete(manager.start())
    except KeyboardInterrupt:
        try:
            loop.run_until_complete(manager.bailout())
        except CException as exc:
            print(str(exc))

if __name__ == '__main__':
    main()
