""" Helper structures and methods
"""

import os
import time
from enum import Enum

# read file at <path> and extract envvars from it
def get_env_data_as_dict(path: str) -> dict:
  env = {}
  with open(path, 'r') as f:
    for line in f.readlines():
      line = line.strip()
      if line.startswith('#') or line == '':
        continue
      k, v = line.strip().split('=')
      env[k.strip()] = v.strip()
  return env


# format float to 8 decimals with stripped zeros
def ffmt(val: float, decimals: int = 8) -> str:
  return f'{val:.{decimals}f}'


# current timestamp in milliseconds
def tstamp() -> int:
  return time.time_ns() // 1000000


# UTF-8 string to bytes
def bencode(val: str) -> bytes:
  return bytes(val, 'UTF-8')


# sell strategy enum
class SellStrategy(Enum):
  LIMIT = 'LIMIT'
  MARKET = 'MARKET'
  HYBRID = 'HYBRID'


# pretty printing
class CColors(Enum):
  OKBLUE = '\033[94m'
  OKCYAN = '\033[96m'
  OKGREEN = '\033[92m'
  WARNING = '\033[93m'
  FAIL = '\033[91m'
  ENDC = '\033[0m'

  @classmethod
  def cstr(cls, val: str, col) -> str:
    return f'{col.value}{val}{cls.ENDC.value}'

  @classmethod
  def cprint(cls, val: str, col):
    print(cls.cstr(val, col))

  @classmethod
  def iprint(cls, val: str):
    cls.cprint(f'[INFO] {val}', cls.OKBLUE)

  @classmethod
  def eprint(cls, val: str):
    cls.cprint(f'[FAIL] {val}', cls.FAIL)

  @classmethod
  def wprint(cls, val: str):
    cls.cprint(f'[WARN] {val}', cls.WARNING)

  @classmethod
  def oprint(cls, val: str):
    cls.cprint(f'[OK] {val}', cls.OKGREEN)


# colored value error
class CException(Exception):
  def __init__(self, msg):
    super().__init__(CColors.cstr(f'[ERROR] {msg}', CColors.FAIL))


# exception when an invalid trading pair is chosen
class InvalidPair(CException):
  pass


# global environment
class Environment:
  def __init__(self, f: str = '.env'):
    self.raw = get_env_data_as_dict(
      os.path.join(os.path.dirname(os.path.realpath(__file__)), f))
    self.conn = self.raw['SERVER_HOST'], int(self.raw['SERVER_PORT'])

    self.override   = bool(int(self.raw['DEFAULT_OVERRIDE']))
    self.qcoin      = self.raw['DEFAULT_QCOIN']
    self.buy_perc   = float(self.raw['DEFAULT_BUY_PERC'])
    self.sell_perc  = float(self.raw['DEFAULT_SELL_PERC'])
    self.profit     = float(self.raw['DEFAULT_PROFIT'])
    self.stop       = float(self.raw['DEFAULT_STOP_LEVEL'])
    self.sell_strat = SellStrategy(self.raw['DEFAULT_SELL_STRATEGY'])
    if self.sell_strat == SellStrategy.MARKET:
      self.min_profit = self.profit
    else:
      self.min_profit = float(self.raw['DEFAULT_MIN_LIMIT_PROFIT'])

  def __getitem__(self, key):
    return self.raw[key]
