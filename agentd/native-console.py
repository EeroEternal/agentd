#!/usr/bin/python
import fcntl
import os
import termios

fd = os.open("/dev/tty1", os.O_RDWR)
fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
os.tcsetpgrp(fd, os.getpid())
os.dup2(fd, 0)
os.dup2(fd, 1)
os.dup2(fd, 2)
attributes = termios.tcgetattr(fd)
attributes[0] |= termios.ICRNL
attributes[1] |= termios.ONLCR
attributes[2] |= termios.CREAD
attributes[3] |= termios.ECHO | termios.ECHOE | termios.ECHOK | termios.ICANON | termios.ISIG
attributes[6][termios.VMIN] = 1
attributes[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, attributes)
os.execv("/usr/bin/bash", ["/usr/bin/bash", "--login"])
