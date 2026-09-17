"""Top-level compatibility module for the WASI socket facade."""

from wasi_runtime._socket import *  # noqa: F401,F403
from wasi_runtime._socket import socket as SocketType

has_ipv6 = False
