from http.server import BaseHTTPRequestHandler, HTTPServer
import time

class MyHandler(BaseHTTPRequestHandler):
  def do_POST(self):
    len = int(self.headers.get('Content-Length'))
    self.server.accept_coin(self.rfile.read(len).decode('utf-8').upper())
    self.send_response(200)
    self.send_header('Content-type', 'application/json')
    self.end_headers()
    self.wfile.write(bytes('{"status": "ok"}', 'utf-8'))


class MyServer(HTTPServer):
  def __init__(self, manager, *args, **kwargs):
    # Because HTTPServer is an old-style class, super() can't be used.
    HTTPServer.__init__(self, *args, **kwargs)
    self.manager = manager
  
  def accept_coin(self, coin):
    self.manager.start(coin)


class HTTPCoinAcceptor:
  def __init__(self, manager, host = 'localhost', port = 1337):
    self.srv = MyServer(manager, (host, port), MyHandler)
  
  def start(self):
    try:
      self.srv.serve_forever()
    except KeyboardInterrupt:
      self.stop()
  
  def stop(self):
    self.srv.server_close()
