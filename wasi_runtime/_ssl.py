"""Pure-Python _ssl facade; TLS is terminated by the a-Shell loader."""
from __future__ import annotations
import wasi_loader
CERT_NONE, CERT_OPTIONAL, CERT_REQUIRED = 0, 1, 2
PROTOCOL_TLS, PROTOCOL_TLS_CLIENT, PROTOCOL_TLS_SERVER = 2, 16, 17
OPENSSL_VERSION = "a-Shell loader TLS"
OPENSSL_VERSION_NUMBER = 0
OPENSSL_VERSION_INFO = (0, 0, 0, 0, 0)
HAS_SNI = True
HAS_ALPN = True
HAS_ECDH = True
HAS_NPN = False
class SSLError(OSError): pass
class SSLCertVerificationError(SSLError): pass
class SSLContext:
    def __init__(self, protocol=PROTOCOL_TLS): self.protocol, self.verify_mode, self.check_hostname = protocol, CERT_NONE, False
    def load_verify_locations(self, cafile=None, capath=None, cadata=None): return None
    def set_alpn_protocols(self, protocols): self.alpn_protocols = list(protocols)
    def set_ciphers(self, ciphers): self.ciphers = ciphers
    def set_default_verify_paths(self): return None
    def load_default_certs(self, purpose=None): return None
    def wrap_socket(self, sock, server_side=False, server_hostname=None, **kwargs):
        wasi_loader.call("socket.start_tls", {"id": sock.fileno(), "server_side": server_side, "server_hostname": server_hostname, "verify_mode": self.verify_mode, "check_hostname": self.check_hostname})
        return sock
def create_default_context(purpose=None, cafile=None, capath=None, cadata=None):
    context = SSLContext(PROTOCOL_TLS_CLIENT); context.verify_mode = CERT_REQUIRED; context.check_hostname = True
    if cafile or capath or cadata: context.load_verify_locations(cafile, capath, cadata)
    return context
def _create_unverified_context(protocol=PROTOCOL_TLS, *args, **kwargs):
    context = SSLContext(protocol); context.check_hostname = False; context.verify_mode = CERT_NONE
    return context
def wrap_socket(sock, keyfile=None, certfile=None, server_side=False, cert_reqs=CERT_NONE, ssl_version=PROTOCOL_TLS, ca_certs=None, server_hostname=None, **kwargs):
    context = SSLContext(ssl_version); context.verify_mode = cert_reqs; context.check_hostname = server_hostname is not None
    return context.wrap_socket(sock, server_side=server_side, server_hostname=server_hostname, **kwargs)
def RAND_bytes(n): return wasi_loader.call("crypto.random", {"size": n}).get("data", b"")
_SSLContext = SSLContext
