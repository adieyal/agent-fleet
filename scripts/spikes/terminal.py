"""Minimal terminal query replies for the isolated interactive probes."""

import fcntl
import os
import struct
import termios
import tty


def acquire_terminal() -> None:
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class Terminal:
    def __init__(self, master: int, slave: int) -> None:
        self.master = master
        self.pending = b""
        tty.setraw(slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    def respond(self, chunk: bytes) -> None:
        self.pending += chunk
        for query, reply in (
            (b"\x1b[6n", b"\x1b[1;1R"),
            (b"\x1b[c", b"\x1b[?1;2c"),
            (b"\x1b[?u", b"\x1b[?0u"),
            (b"\x1b]10;?\x1b\\", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"),
            (b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
        ):
            while query in self.pending:
                os.write(self.master, reply)
                self.pending = self.pending.replace(query, b"", 1)
        self.pending = self.pending[-16:]
