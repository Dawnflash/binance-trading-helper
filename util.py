""" Helper structures and methods
"""

import os
import time
from enum import Enum

def ffmt(val: float, decimals: int = 8) -> str:
    """ format float to 8 decimals with stripped zeros """
    return f'{val:.{decimals}f}'

def tstamp() -> int:
    """ current timestamp in milliseconds """
    return time.time_ns() // 1000000

def bencode(val: str) -> bytes:
    """ UTF-8 string to bytes """
    return bytes(val, 'UTF-8')

class SellType(Enum):
    """ sell strategy enum """
    LIMIT = 'LIMIT'
    MARKET = 'MARKET'
    HYBRID = 'HYBRID'

class CColors(Enum):
    """ class supporting colored printing and strings """
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'

    @classmethod
    def cstr(cls, val: str, col) -> str:
        """ return colored string """
        return f'{col.value}{val}{cls.ENDC.value}'

    @classmethod
    def cprint(cls, val: str, col):
        """ print colored message """
        print(cls.cstr(val, col))

    @classmethod
    def iprint(cls, val: str):
        """ print info message """
        cls.cprint(f'[INFO] {val}', cls.OKBLUE)

    @classmethod
    def eprint(cls, val: str):
        """ print error message """
        cls.cprint(f'[FAIL] {val}', cls.FAIL)

    @classmethod
    def wprint(cls, val: str):
        """ print warning message """
        cls.cprint(f'[WARN] {val}', cls.WARNING)

    @classmethod
    def oprint(cls, val: str):
        """ print OK message """
        cls.cprint(f'[OK] {val}', cls.OKGREEN)

class CException(Exception):
    """ colored value error """
    def __init__(self, msg):
        super().__init__(CColors.cstr(f'[ERROR] {msg}', CColors.FAIL))


class InvalidPair(CException):
    """ exception when an invalid trading pair is chosen """

class Environment(dict):
    """ global environment, raw values are stored as dictionary values """
    def __init__(self, fname: str, *args, **kwargs):
        """ <fname>: name of the environment file """
        super().__init__(*args, **kwargs)
        self.set_from_env(fname)

        self.conn = self['SERVER_HOST'], int(self['SERVER_PORT'])
        self.bailout = bool(self['BAILOUT'])
        self.usd_value  = float(self['BUY_VALUE_USD'])
        self.src_coins  = [coin.strip().upper() for coin
                           in self['SOURCE_COINS'].split(',')]

        self.override   = bool(int(self['PROMPT_OVERRIDE']))
        self.qcoin      = self['DEFAULT_QCOIN'].upper()
        self.buy_perc   = float(self['DEFAULT_BUY_PERC'])
        self.sell_type  = SellType(self['DEFAULT_SELL_TYPE'])
        self.profit     = float(self['DEFAULT_PROFIT'])
        self.stop       = float(self['DEFAULT_STOP_LEVEL'])

    def set_from_env(self, fname: str):
        """ read <fname> in the script's directory and extract envvars from it
            use variables from the environment if applicable """
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)), fname)
        with open(path, 'r') as file:
            for line in file.readlines():
                line = line.strip()
                if line.startswith('#') or line == '':
                    continue
                key, val = (s.strip() for s in line.strip().split('='))
                self[key] = os.getenv(key, val)
