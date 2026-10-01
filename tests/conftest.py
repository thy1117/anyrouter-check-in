import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


import socket

import pytest


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
	"""Tests must use mock transports, never real accounts, sites or webhooks."""
	original = socket.socket.connect

	def connect(sock, address):
		if sock.family in (socket.AF_INET, socket.AF_INET6):
			raise AssertionError('Real network connections are forbidden in tests')
		return original(sock, address)

	monkeypatch.setattr(socket.socket, 'connect', connect)
