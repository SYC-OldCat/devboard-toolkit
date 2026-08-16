"""跨平台交互式 shell(参考 paramiko 官方 demo)"""

import sys
import socket
import threading


def interactive_shell(chan):
    try:
        import termios  # noqa
        import tty      # noqa
        has_termios = True
    except ImportError:
        has_termios = False

    if has_termios:
        _posix_shell(chan)
    else:
        _windows_shell(chan)


def _posix_shell(chan):
    import select
    import termios
    import tty

    oldtty = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
        chan.settimeout(0.0)
        while True:
            r, _, _ = select.select([chan, sys.stdin], [], [])
            if chan in r:
                try:
                    x = chan.recv(1024)
                    if len(x) == 0:
                        sys.stdout.write("\r\n*** EOF\r\n")
                        sys.stdout.flush()
                        break
                    sys.stdout.buffer.write(x)
                    sys.stdout.flush()
                except socket.timeout:
                    pass
            if sys.stdin in r:
                x = sys.stdin.read(1)
                if len(x) == 0:
                    break
                chan.send(x)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, oldtty)


def _windows_shell(chan):
    sys.stdout.write(
        "Line-buffered terminal emulation. Press F6 or Ctrl+Z then Enter to exit.\r\n\r\n"
    )
    sys.stdout.flush()

    def writeall(sock):
        while True:
            data = sock.recv(256)
            if not data:
                sys.stdout.write("\r\n*** EOF ***\r\n\r\n")
                sys.stdout.flush()
                break
            sys.stdout.write(data.decode(errors="replace"))
            sys.stdout.flush()

    writer = threading.Thread(target=writeall, args=(chan,), daemon=True)
    writer.start()

    try:
        while True:
            d = sys.stdin.read(1)
            if not d:
                break
            chan.send(d)
    except EOFError:
        pass
