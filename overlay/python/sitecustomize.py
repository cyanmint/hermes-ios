"""Install WASI process facades before application imports run."""

import socket as _stdlib_socket

import wasi_loader
from wasi_runtime import _socket as _wasi_socket


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
            "getaddrinfo", "gethostname", "getdefaulttimeout", "setdefaulttimeout",
        }:
            setattr(_stdlib_socket, name, getattr(_wasi_socket, name))
    _stdlib_socket.SocketType = _wasi_socket.socket
    _stdlib_socket.has_ipv6 = False


_install_socket_facade()
wasi_loader.install_stdio()
