import os
import time

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


# exception when an invalid trading pair is chosen
class InvalidPair(ValueError):
  pass


# global environment
class Environment:
  def __init__(self, f: str = '.env'):
    self.raw = get_env_data_as_dict(os.path.join(os.path.dirname(os.path.realpath(__file__)), f))
    self.conn = self.raw['SERVER_HOST'], int(self.raw['SERVER_PORT'])
    self.qcoin = self.raw['DEFAULT_QCOIN']
    self.profit = float(self.raw['DEFAULT_PROFIT'])
    self.qbalperc = float(self.raw['DEFAULT_QBAL_PERC'])
    self.limit_redc = float(self.raw['DEFAULT_LIMIT_REDC'])
  
  def __getitem__(self, key):
    return self.raw[key]
