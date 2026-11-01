#!/usr/bin/env python3
"""ProxyCommand helper: tunnel a TCP connection through the egress
HTTP proxy (HTTPS_PROXY env) using CONNECT, then relay stdio <-> socket.
Usage: proxy_connect.py <host> <port>"""
import base64
import os
import socket
import sys
import threading
import urllib.parse


def main() -> None:
    host, port = sys.argv[1], int(sys.argv[2])
    proxy = urllib.parse.urlparse(os.environ["HTTPS_PROXY"])
    s = socket.create_connection((proxy.hostname, proxy.port), timeout=30)
    auth = ""
    if proxy.username:
        cred = f"{urllib.parse.unquote(proxy.username)}:" \
               f"{urllib.parse.unquote(proxy.password or '')}"
        auth = "Proxy-Authorization: Basic " + \
            base64.b64encode(cred.encode()).decode() + "\r\n"
    s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
              f"{auth}\r\n".encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            sys.exit("proxy closed during CONNECT")
        buf += chunk
    if b" 200 " not in buf.split(b"\r\n", 1)[0]:
        sys.exit(f"CONNECT refused: {buf.split(chr(13).encode(), 1)[0]!r}")

    def pump_in() -> None:
        try:
            while True:
                data = sys.stdin.buffer.read1(65536)
                if not data:
                    break
                s.sendall(data)
        except Exception:
            pass
        try:
            s.shutdown(socket.SHUT_WR)
        except Exception:
            pass

    threading.Thread(target=pump_in, daemon=True).start()
    try:
        while True:
            data = s.recv(65536)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except Exception:
        pass


if __name__ == "__main__":
    main()
