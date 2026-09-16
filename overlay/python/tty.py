"""tty compatibility layer paired with the WASI termios shim."""

LFLAG = 3
IFLAG = 0
CC = 6


def setraw(_fd, when=None):
    return None


def setcbreak(_fd, when=None):
    return None
