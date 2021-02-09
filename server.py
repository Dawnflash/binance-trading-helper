""" A simple HTTP server listening for POST requests
Each incoming POST request is assumed to carry a (case-insensitive) coin name
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from util import InvalidPair


class MyHandler(BaseHTTPRequestHandler):
    """ HTTP request handler """
    def do_POST(self):
        """ handle incoming POST and extract coin name from it """
        size = int(self.headers.get('Content-Length'))
        coin = self.rfile.read(size).decode('utf-8').upper()
        code, status = self.server.accept_coin(coin)
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(bytes(f'{{"status": "{status}"}}\n', 'utf-8'))

class MyServer(HTTPServer):
    """ A simple HTTP server carrying a CoinAcceptor handle """
    def __init__(self, acceptor, *args, **kwargs):
        # Because HTTPServer is an old-style class, super() can't be used.
        HTTPServer.__init__(self, *args, **kwargs)
        self.acceptor = acceptor

    def accept_coin(self, coin: str) -> (int, str):
        """ pass status message from CoinAcceptor """
        return self.acceptor.accept(coin)

class HTTPCoinAcceptor:
    """ HTTP server manager carrying a MarketManager handle """
    def __init__(self, manager, conn):
        self.srv = MyServer(self, conn, MyHandler)
        self.mgr = manager

    def start(self):
        """ start HTTP server"""
        try:
            self.srv.serve_forever()
        except Exception as exc:
            print(str(exc))
            self.stop()

    def accept(self, coin: str) -> (int, str):
        """ lock in a coin and return HTTP code and status message """
        try:
            self.mgr.lock(coin)
            return 200, 'ok'
        except InvalidPair as exc:
            print(str(exc))
            return 400, 'invalid_symbol'
        except Exception as exc:
            print(str(exc))
            return 200, 'closed'

    def stop(self):
        """ stop the server """
        self.srv.server_close()
