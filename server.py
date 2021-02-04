from http.server import BaseHTTPRequestHandler, HTTPServer
from util import InvalidPair
import time

class MyHandler(BaseHTTPRequestHandler):
  def do_POST(self):
    len = int(self.headers.get('Content-Length'))
    self.send_response(200)
    self.send_header('Content-type', 'application/json')
    self.end_headers()
    self.wfile.write(bytes('{"status": "ok"}', 'utf-8'))
    self.server.accept_coin(self.rfile.read(len).decode('utf-8').upper())


class MyServer(HTTPServer):
  def __init__(self, acceptor, *args, **kwargs):
    # Because HTTPServer is an old-style class, super() can't be used.
    HTTPServer.__init__(self, *args, **kwargs)
    self.acceptor = acceptor
  
  def accept_coin(self, coin: str):
    self.acceptor.accept(coin)


class HTTPCoinAcceptor:
  def __init__(self, manager, conn):
    self.srv = MyServer(self, conn, MyHandler)
    self.mgr = manager
  
  def start(self):
    try:
      self.srv.serve_forever()
    except KeyboardInterrupt:
      self.stop()
    except Exception as e:
      print(str(e))
      self.stop()
  
  def accept(self, coin: str):
    try:
      self.mgr.start(coin)
      self.stop()
    except InvalidPair as e:
      print(str(e))
    except Exception as e:
      self.stop()
      raise e
  
  def stop(self):
    print('closing')
    self.srv.server_close()
