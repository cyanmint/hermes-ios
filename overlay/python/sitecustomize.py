"""Install WASI process facades before application imports run."""

import socket as _stdlib_socket
import selectors as _selectors
import signal as _signal

import wasi_loader
from wasi_runtime import _socket as _wasi_socket

if not hasattr(_signal, "valid_signals"):
    _signal.valid_signals = lambda: {
        value for name, value in vars(_signal).items()
        if name.startswith("SIG") and name[3:].isupper() and isinstance(value, int)
    }
_signal.set_wakeup_fd = lambda _fd: -1
if not hasattr(_signal, "siginterrupt"):
    _signal.siginterrupt = lambda _sig, _flag: None


def _install_socket_facade() -> None:
    """Make the stdlib ``socket`` namespace use the WASI broker facade.

    CPython's pure-Python ``socket`` module imports the builtin ``_socket``
    extension first.  On WASI that extension is present only as a partial
    compatibility module, so constants such as ``AI_PASSIVE`` are missing even
    though our broker facade provides them.  Patching the public namespace here
    keeps asyncio and third-party code on one consistent implementation.
    """
    for name in dir(_wasi_socket):
        if name.isupper() or name in {
            "error", "timeout", "gaierror", "socket", "create_connection",
            "getaddrinfo", "gethostbyaddr", "gethostname", "getdefaulttimeout", "setdefaulttimeout",
        }:
            setattr(_stdlib_socket, name, getattr(_wasi_socket, name))
    _stdlib_socket.SocketType = _wasi_socket.socket
    _stdlib_socket.has_ipv6 = False


class _BrokerSelector:
    """selectors.DefaultSelector implementation for broker-backed sockets."""
    def __init__(self):
        self._items = {}

    def register(self, fileobj, events, data=None):
        fd = fileobj if isinstance(fileobj, int) else fileobj.fileno()
        key = _selectors.SelectorKey(fileobj, fd, events, data)
        self._items[fileobj] = key
        return key

    def unregister(self, fileobj):
        return self._items.pop(fileobj)

    def modify(self, fileobj, events, data=None):
        self.unregister(fileobj)
        return self.register(fileobj, events, data)

    def select(self, timeout=None):
        if not self._items:
            if timeout:
                import time
                time.sleep(timeout)
            return []
        sockets = [item for item in self._items.values() if not isinstance(item.fileobj, int)]
        ready_ids = set(_wasi_socket.poll([item.fileobj.fileno() for item in sockets], timeout)) if sockets else set()
        return [(key, key.events & _selectors.EVENT_READ, key.data)
                for key in sockets if key.fileobj.fileno() in ready_ids]

    def get_map(self):
        return self._items

    def close(self):
        self._items.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


_install_socket_facade()
_selectors.DefaultSelector = _BrokerSelector
_selectors.PollSelector = _BrokerSelector
if hasattr(_selectors, "EpollSelector"):
    _selectors.EpollSelector = _BrokerSelector
if hasattr(_selectors, "SelectSelector"):
    _selectors.SelectSelector = _BrokerSelector
wasi_loader.install_stdio()
