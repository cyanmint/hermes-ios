"""Small terminal API compatibility layer for the a-Shell WASI target."""

class error(OSError):
    pass

TCSANOW = 0
TCSADRAIN = 1
TCSAFLUSH = 2

ECHO = 0x0008
ICANON = 0x0100
ISIG = 0x0080
IEXTEN = 0x0400
IXON = 0x0200
IXOFF = 0x1000
ICRNL = 0x0100
INLCR = 0x0040
IGNCR = 0x0080
VMIN = 6
VTIME = 5


def tcgetattr(_fd):
    return [0, 0, 0, ECHO | ICANON | ISIG | IEXTEN, 0, 0, [0] * 32]


def tcsetattr(_fd, _when, _attributes):
    return None
