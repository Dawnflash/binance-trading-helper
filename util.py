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


# exception when an invalid trading pair is chosen
class InvalidPair(ValueError):
  pass


# sell strategy enum
class SellStrategy(Enum):
  LIMIT = 'LIMIT'
  MARKET = 'MARKET'
  HYBRID = 'HYBRID'


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
    self.sell_strat = SellStrategy(self.raw['DEFAULT_SELL_STRATEGY'])
    if self.sell_strat == SellStrategy.MARKET:
      self.min_profit = self.profit
      self.inc_limit  = False
    else:
      self.min_profit = float(self.raw['DEFAULT_MIN_PROFIT'])
      self.inc_limit  = bool(int(self.raw['DEFAULT_ALLOW_LIMIT_INCREASE']))
    self.qbalance   = 0.0

  def __getitem__(self, key):
    return self.raw[key]
