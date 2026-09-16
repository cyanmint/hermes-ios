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
HAS_SSLv2 = False
HAS_SSLv3 = False
HAS_TLSv1 = True
HAS_TLSv1_1 = True
HAS_TLSv1_2 = True
HAS_TLSv1_3 = True
HAS_PSK = False
_DEFAULT_CIPHERS = "DEFAULT"
_OPENSSL_API_VERSION = "a-Shell-loader"
PROTO_MINIMUM_SUPPORTED = 0
PROTO_SSLv3 = 0
PROTO_TLSv1 = 1
PROTO_TLSv1_1 = 2
PROTO_TLSv1_2 = 3
PROTO_TLSv1_3 = 4
PROTO_MAXIMUM_SUPPORTED = 5
HOSTFLAG_NEVER_CHECK_SUBJECT = 0
VERIFY_X509_PARTIAL_CHAIN = 0
VERIFY_X509_STRICT = 0
ENCODING_DER = 0
OP_ALL = 0
OP_NO_SSLv2 = 0
OP_NO_SSLv3 = 0
OP_NO_COMPRESSION = 0
SSL_ERROR_ZERO_RETURN = 6
SSL_ERROR_WANT_READ = 2
SSL_ERROR_WANT_WRITE = 3
SSL_ERROR_SYSCALL = 5
SSL_ERROR_SSL = 1
VERIFY_DEFAULT = 0
VERIFY_CRL_CHECK_LEAF = 1
VERIFY_CRL_CHECK_CHAIN = 2
VERIFY_X509_TRUSTED_FIRST = 0
class SSLError(OSError): pass
class SSLZeroReturnError(SSLError): pass
class SSLWantReadError(SSLError): pass
class SSLWantWriteError(SSLError): pass
class SSLSyscallError(SSLError): pass
class SSLEOFError(SSLError): pass
class SSLCertVerificationError(SSLError): pass
class SSLSession: pass
class MemoryBIO:
    def __init__(self): self._data = bytearray()
    def write(self, data): self._data.extend(data); return len(data)
    def read(self, size=-1):
        size = len(self._data) if size < 0 else size
        data = bytes(self._data[:size]); del self._data[:size]; return data
    def pending(self): return len(self._data)
    def eof(self): return False
class SSLContext:
    def __new__(cls, protocol=PROTOCOL_TLS): return object.__new__(cls)
    def __init__(self, protocol=PROTOCOL_TLS): self._protocol, self._verify_mode, self._check_hostname = protocol, CERT_NONE, False
    protocol = property(lambda self: self._protocol)
    verify_mode = property(lambda self: self._verify_mode, lambda self, value: setattr(self, "_verify_mode", value))
    check_hostname = property(lambda self: self._check_hostname, lambda self, value: setattr(self, "_check_hostname", bool(value)))
    options = property(lambda self: getattr(self, "_options", OP_ALL), lambda self, value: setattr(self, "_options", value))
    verify_flags = property(lambda self: getattr(self, "_verify_flags", 0), lambda self, value: setattr(self, "_verify_flags", value))
    minimum_version = property(lambda self: getattr(self, "_minimum_version", PROTO_MINIMUM_SUPPORTED), lambda self, value: setattr(self, "_minimum_version", value))
    maximum_version = property(lambda self: getattr(self, "_maximum_version", PROTO_MAXIMUM_SUPPORTED), lambda self, value: setattr(self, "_maximum_version", value))
    post_handshake_auth = property(lambda self: getattr(self, "_post_handshake_auth", False), lambda self, value: setattr(self, "_post_handshake_auth", bool(value)))
    def _set_alpn_protocols(self, protocols):
        # Lib/ssl.py passes the native _ssl method a length-prefixed byte
        # sequence, not the original list of strings.
        data = bytes(protocols)
        decoded = []
        index = 0
        while index < len(data):
            length = data[index]
            index += 1
            if length == 0 or index + length > len(data):
                raise SSLError("invalid ALPN protocol list")
            decoded.append(data[index:index + length].decode("ascii"))
            index += length
        self.alpn_protocols = decoded
    def _wrap_socket(self, sock, server_side=False, server_hostname=None, owner=None, session=None):
        raw = __import__("_socket").socket(fileno=sock.fileno())
        wasi_loader.call("socket.start_tls", {"id": raw.fileno(), "server_side": server_side, "server_hostname": server_hostname, "verify_mode": self.verify_mode, "check_hostname": self.check_hostname})
        return _TLSObject(raw, self)
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
def RAND_bytes(n):
    import base64
    return base64.b64decode(wasi_loader.call("crypto.random", {"size": n}).get("data", ""))
_SSLContext = SSLContext
class _TLSObject:
    def __init__(self, sock, context): self.sock, self.context = sock, context; self.session = None; self.session_reused = False
    def read(self, size=1024, buffer=None):
        data = self.sock.recv(size)
        if buffer is not None: buffer[:len(data)] = data; return len(data)
        return data
    def write(self, data): return self.sock.send(data)
    def do_handshake(self): return None
    def shutdown(self): return None
    def unwrap(self): return self.sock
    def getpeercert(self, binary_form=False): return b"" if binary_form else {}
    def cipher(self): return ("LOADER-TLS", "TLS", 0)
    def version(self): return "TLS"
    def selected_alpn_protocol(self): return None
def RAND_status(): return True
def RAND_add(data, entropy): return None
def txt2obj(value, name=False): return (0, str(value), str(value), str(value))
def nid2obj(value): return (int(value), str(value), str(value), str(value))
def enum_certificates(store_name): return []
def enum_crls(store_name): return []
def get_default_verify_paths(): return ("SSL_CERT_FILE", None, "SSL_CERT_DIR", None)
