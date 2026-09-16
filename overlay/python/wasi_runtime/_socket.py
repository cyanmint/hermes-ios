"""Pure-Python _socket facade; all operations execute in the a-Shell loader."""
from __future__ import annotations
import base64
import wasi_loader
AF_UNSPEC, AF_INET, AF_INET6, AF_UNIX = 0, 2, 10, 1
SOCK_STREAM, SOCK_DGRAM, SOCK_RAW = 1, 2, 3
SOL_SOCKET, SO_REUSEADDR, SO_KEEPALIVE, SO_TYPE = 1, 2, 9, 3
IPPROTO_TCP, TCP_NODELAY = 6, 1
AI_PASSIVE, AI_CANONNAME, AI_NUMERICHOST = 1, 2, 4
AI_V4MAPPED, AI_ALL, AI_ADDRCONFIG = 8, 16, 32
SOMAXCONN = 128
SHUT_RD, SHUT_WR, SHUT_RDWR = 0, 1, 2
_default_timeout = None
class error(OSError): pass
class timeout(error): pass
class gaierror(error): pass
def getdefaulttimeout(): return _default_timeout
def setdefaulttimeout(value):
    global _default_timeout
    if value is not None and value < 0: raise ValueError("Timeout value out of range")
    _default_timeout = value
def gethostname(): return wasi_loader.call("socket.hostname").get("name", "localhost")
def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    result = wasi_loader.call("socket.resolve", {"host": host, "port": port, "family": family, "type": type, "proto": proto, "flags": flags})
    return [tuple(item) for item in result.get("addresses", [])]
class socket:
    __slots__ = ("_id", "_family", "_type", "_proto", "timeout", "_closed")
    def __init__(self, family=AF_INET, type=SOCK_STREAM, proto=0, fileno=None):
        self._id = int(fileno) if fileno is not None else wasi_loader.call("socket.open", {"family": family, "type": type, "proto": proto}).get("id")
        self._family, self._type, self._proto, self.timeout = family, type, proto, _default_timeout
        self._closed = False
    @property
    def family(self): return self._family
    @property
    def type(self): return self._type
    @property
    def proto(self): return self._proto
    def connect(self, address):
        host, port = address[:2]
        wasi_loader.call("socket.connect", {"id": self._id, "host": host, "port": port, "timeout": self.timeout})
    def bind(self, address):
        host, port = address[:2]
        wasi_loader.call("socket.bind", {"id": self._id, "host": host, "port": port})
    def listen(self, backlog=0):
        wasi_loader.call("socket.listen", {"id": self._id, "backlog": int(backlog)})
    def accept(self):
        result = wasi_loader.call("socket.accept", {"id": self._id, "timeout": self.timeout})
        return socket(fileno=int(result["id"])), tuple(result["address"])
    def send(self, data, flags=0):
        return int(wasi_loader.call("socket.send", {"id": self._id, "data": {"encoding": "base64", "data": base64.b64encode(bytes(data)).decode("ascii")}, "flags": flags}).get("sent", 0))
    def sendall(self, data, flags=0):
        wasi_loader.call("socket.sendall", {"id": self._id, "data": {"encoding": "base64", "data": base64.b64encode(bytes(data)).decode("ascii")}, "flags": flags})
    def recv(self, bufsize, flags=0):
        return base64.b64decode(wasi_loader.call("socket.recv", {"id": self._id, "size": bufsize, "flags": flags, "timeout": self.timeout}).get("data", ""))
    def recv_into(self, buffer, nbytes=0, flags=0):
        data = self.recv(nbytes or len(buffer), flags); buffer[:len(data)] = data; return len(data)
    def settimeout(self, value):
        self.timeout = value; wasi_loader.call("socket.timeout", {"id": self._id, "timeout": value})
    def gettimeout(self): return self.timeout
    def setblocking(self, flag): self.settimeout(None if flag else 0.0)
    def getsockopt(self, level, option, *args): return wasi_loader.call("socket.getopt", {"id": self._id, "level": level, "option": option}).get("value", 0)
    def setsockopt(self, level, option, value, *args): wasi_loader.call("socket.setopt", {"id": self._id, "level": level, "option": option, "value": value})
    def getsockname(self): return tuple(wasi_loader.call("socket.name", {"id": self._id}).get("address", ["0.0.0.0", 0]))
    def getpeername(self): return tuple(wasi_loader.call("socket.peer", {"id": self._id}).get("address", ["0.0.0.0", 0]))
    def shutdown(self, how): wasi_loader.call("socket.shutdown", {"id": self._id, "how": how})
    def close(self):
        if not self._closed: self._closed = True; wasi_loader.call("socket.close", {"id": self._id})
    def fileno(self): return self._id
    def detach(self):
        sid = self._id
        self._id = -1
        self._closed = True
        return sid
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def makefile(self, mode="r", buffering=None, **kwargs): return _SocketFile(self, mode)
def create_connection(address, timeout=_default_timeout, source_address=None):
    sock = socket(); sock.settimeout(timeout)
    try: sock.connect(address)
    except Exception: sock.close(); raise
    return sock
class _SocketFile:
    def __init__(self, sock, mode): self.sock, self.mode = sock, mode
    def read(self, size=-1): return self.sock.recv(65536 if size < 0 else size)
    def readinto(self, buf): return self.sock.recv_into(buf)
    def write(self, data): return self.sock.send(data)
    def flush(self): pass
    def close(self): self.sock.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
