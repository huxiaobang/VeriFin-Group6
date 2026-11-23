"""Block sockets in the CLI under test, proving no API fallback is attempted."""
import os,socket
from pathlib import Path

def blocked(*args,**kwargs):
 path=os.environ.get("QA_NETWORK_ATTEMPTS")
 if path:Path(path).write_text("network attempted")
 raise RuntimeError("QA forbids all network connections")
socket.socket.connect=blocked
socket.create_connection=blocked
socket.socket.connect_ex=blocked
socket.socket.sendto=blocked
socket.getaddrinfo=blocked
