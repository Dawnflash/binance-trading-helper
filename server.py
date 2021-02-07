""" A simple HTTP server listening for POST requests
Each incoming POST request is assumed to carry a (case-insensitive) coin name
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from util import InvalidPair

# POST request handler
class MyHandler(BaseHTTPRequestHandler):
  def do_POST(self):
    len = int(self.headers.get('Content-Length'))
    coin = self.rfile.read(len).decode('utf-8').upper()
    code, status = self.server.accept_coin(coin)
    self.send_response(code)
    self.send_header('Content-type', 'application/json')
    self.end_headers()
    self.wfile.write(bytes(f'{{"status": "{status}"}}\n', 'utf-8'))


# A simple HTTP server carrying a CoinAcceptor handle
class MyServer(HTTPServer):
  def __init__(self, acceptor, *args, **kwargs):
    # Because HTTPServer is an old-style class, super() can't be used.
    HTTPServer.__init__(self, *args, **kwargs)
    self.acceptor = acceptor

  # pass status message from CoinAcceptor
  def accept_coin(self, coin: str) -> (int, str):
    return self.acceptor.accept(coin)


# HTTP server manager carrying a MarketManager handle
class HTTPCoinAcceptor:
  def __init__(self, manager, conn):
    self.srv = MyServer(self, conn, MyHandler)
    self.mgr = manager

  def start(self):
    try:
      self.srv.serve_forever()
    except Exception as e:
      print(str(e))
      self.stop()

  # lock in a coin and return HTTP code and status message
  def accept(self, coin: str) -> (int, str):
    try:
      self.mgr.lock(coin)
      return 200, 'ok'
    except InvalidPair as e:
      print(str(e))
      return 400, 'invalid_symbol'
    except Exception as e:
      print(str(e))
      return 200, 'closed'

  def stop(self):
    self.srv.server_close()
