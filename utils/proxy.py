"""代理配置：读取环境变量并供浏览器 / HTTP 客户端使用。"""

from __future__ import annotations

import ipaddress
import json
import os
import threading
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import httpx

from utils.debug import debug_print


class ProxyNodeSwitchError(RuntimeError):
	"""Mihomo 未能切到账号指定节点时抛出，防止账号误用其他出口。"""


class ProxyNodeAllocator:
	"""跨运行分配节点，保证同站不同账号滚动 24 小时内不重复。"""

	def __init__(self, nodes, *, state_file=None, now=None):
		self.nodes = list(dict.fromkeys(node for node in nodes if node))
		self.state_file = Path(state_file or os.getenv('PROXY_ASSIGNMENTS_FILE', '.proxy_assignments.json'))
		self.now = now or (lambda: datetime.now(timezone.utc))
		self._lock = threading.Lock()

	def _read(self):
		try:
			return json.loads(self.state_file.read_text(encoding='utf-8'))
		except (OSError, json.JSONDecodeError):
			return {}

	def _write(self, state):
		self.state_file.parent.mkdir(parents=True, exist_ok=True)
		tmp = self.state_file.with_suffix(self.state_file.suffix + '.tmp')
		tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
		tmp.replace(self.state_file)

	def _allocate(self, provider, account, excluded=(), reuse_existing=True, ip_probe=None):
		with self._lock:
			state = self._read()
			now = self.now()
			cutoff = now - timedelta(hours=24)
			assignments = {
				key: value
				for key, value in state.get(provider, {}).items()
				if datetime.fromisoformat(value['assigned_at']) > cutoff
			}

			# Same account keeps its node for the rolling 24h window unless it just failed.
			existing = assignments.get(account)
			if (
				not ip_probe
				and reuse_existing
				and existing
				and existing['node'] not in excluded
				and existing['node'] in self.nodes
			):
				state[provider] = assignments
				self._write(state)
				return existing['node']

			# Excluded (just-failed) nodes are quarantined provider-wide for 24h.
			for bad in excluded:
				assignments[f'__cooldown__:{bad}'] = {'node': bad, 'assigned_at': now.isoformat()}

			taken = {entry['node'] for key, entry in assignments.items() if key != account}
			taken.update(excluded)
			taken_ips = {entry.get('ip') for key, entry in assignments.items() if key != account}
			candidates = list(self.nodes)
			if ip_probe and existing and existing['node'] in candidates:
				candidates.remove(existing['node'])
				candidates.insert(0, existing['node'])
			for node in candidates:
				if node in taken:
					continue
				ip = None
				if ip_probe:
					try:
						ip = ip_probe(node)
					except ProxyNodeSwitchError as exc:
						print(f'[WARN] {account}: Skipping node "{node}": {exc}')
						continue
					if ip in taken_ips:
						print(f'[WARN] {account}: Skipping node "{node}": duplicate exit IP {ip}')
						continue
				assignments[account] = {'node': node, 'assigned_at': now.isoformat()}
				if ip_probe:
					assignments[account]['ip'] = ip
					print(f'[INFO] {account}: Verified distinct exit IP {ip} on "{node}"')
				state[provider] = assignments
				self._write(state)
				return node
			state[provider] = assignments
			self._write(state)
			raise ProxyNodeSwitchError(
				f'Provider {provider}: no reachable node with a distinct exit IP is available'
				if ip_probe
				else f'Provider {provider}: no unused GLaDOS node is available within the last 24 hours'
			)

	def assign(self, provider, account, *, ip_probe=None):
		return self._allocate(provider, account, ip_probe=ip_probe)

	def replace(self, provider, account, failed_node):
		return self._allocate(provider, account, excluded={failed_node}, reuse_existing=False)


def get_mihomo_nodes(group_name: str = 'CHECKIN', controller_port: int | None = None) -> list[str]:
	"""从 Mihomo 运行时订阅组读取节点池。"""
	port = controller_port or int(os.getenv('PROXY_CONTROLLER_PORT', '9090'))
	url = f'http://127.0.0.1:{port}/proxies/{urllib.parse.quote(group_name, safe="")}'
	try:
		with urllib.request.urlopen(url, timeout=5) as resp:
			data = json.loads(resp.read().decode('utf-8'))
		return [item for item in data.get('all', []) if item not in {'DIRECT', 'REJECT'}]
	except Exception as exc:
		debug_print(f'[WARN] Query Mihomo node pool failed: {exc}')
		return []


def get_proxy_server(*, use_proxy: bool = True) -> str | None:
	"""按平台配置读取 CHECKIN_PROXY_URL；use_proxy=False 时不返回代理地址。"""
	if not use_proxy:
		return None
	server = os.getenv('CHECKIN_PROXY_URL', '').strip()
	return server or None


def get_playwright_proxy(*, use_proxy: bool = True) -> dict[str, str] | None:
	server = get_proxy_server(use_proxy=use_proxy)
	if not server:
		return None
	return {'server': server}


def get_current_mihomo_node(
	controller_port: int | None = None,
	group_name: str = 'CHECKIN',
) -> str | None:
	"""查询 Mihomo 当前选中的代理节点名称。"""
	port = controller_port or int(os.getenv('PROXY_CONTROLLER_PORT', '9090'))
	url = f'http://127.0.0.1:{port}/proxies/{urllib.parse.quote(group_name, safe="")}'
	try:
		req = urllib.request.Request(url)
		with urllib.request.urlopen(req, timeout=3) as resp:
			if resp.status == 200:
				data = json.loads(resp.read().decode('utf-8'))
				return data.get('now')
	except Exception as exc:
		debug_print(f'[WARN] Query Mihomo current node failed: {exc}')
	return None


def switch_mihomo_node(
	node_name: str,
	controller_port: int | None = None,
	group_name: str = 'CHECKIN',
) -> bool:
	"""通过 Mihomo External Controller REST API 动态切换选择器组活动节点。"""
	if not node_name:
		return False
	port = controller_port or int(os.getenv('PROXY_CONTROLLER_PORT', '9090'))
	url = f'http://127.0.0.1:{port}/proxies/{urllib.parse.quote(group_name, safe="")}'
	try:
		payload = json.dumps({'name': node_name}).encode('utf-8')
		req = urllib.request.Request(
			url,
			data=payload,
			method='PUT',
			headers={'Content-Type': 'application/json'},
		)
		with urllib.request.urlopen(req, timeout=5) as resp:
			return resp.status in (200, 204)
	except Exception as exc:
		debug_print(f'[WARN] Switch Mihomo node to "{node_name}" failed: {exc}')
		return False


@contextmanager
def active_proxy_node(node_name: str | None, *, account_name: str = '') -> Generator[None, None, None]:
	"""针对单个账号或 provider 的上下文管理器：执行前切换节点，执行完后还原。"""
	if not node_name:
		yield
		return

	prefix = f'{account_name}: ' if account_name else ''
	previous_node = get_current_mihomo_node()
	switched = switch_mihomo_node(node_name)
	if switched:
		print(f'[INFO] {prefix}Switched proxy node to "{node_name}"')
	else:
		raise ProxyNodeSwitchError(f'{prefix}Failed to switch proxy node to "{node_name}"')

	try:
		yield
	finally:
		if switched and previous_node and previous_node != node_name:
			restored = switch_mihomo_node(previous_node)
			if restored:
				print(f'[INFO] {prefix}Restored proxy node to "{previous_node}"')
			else:
				debug_print(f'[WARN] {prefix}Failed to restore proxy node to "{previous_node}"')


def probe_proxy_ip(node_name: str, domain: str) -> str:
	"""确认目标站可达且两次同域 trace 的公网出口一致；不登录、不签到。"""
	server = get_proxy_server()
	if not server:
		raise ProxyNodeSwitchError('Exit IP verification requires CHECKIN_PROXY_URL')
	with active_proxy_node(node_name):
		try:
			with httpx.Client(
				proxy=server,
				trust_env=False,
				follow_redirects=False,
				timeout=10,
				headers={'Cache-Control': 'no-cache', 'Connection': 'close'},
			) as client:

				def read_ip() -> str:
					response = client.get(f'{domain}/cdn-cgi/trace')
					response.raise_for_status()
					fields = dict(line.split('=', 1) for line in response.text.splitlines() if '=' in line)
					ip = ipaddress.ip_address(fields.get('ip', ''))
					if not ip.is_global:
						raise ValueError('Non-public exit IP')
					return str(ip)

				ip = read_ip()
				status = client.get(f'{domain}/api/status')
				status.raise_for_status()
				if status.json().get('success') is not True or read_ip() != ip:
					raise ValueError('Target unavailable or exit IP changed during probe')
				return ip
		except (httpx.HTTPError, ValueError, AttributeError) as exc:
			raise ProxyNodeSwitchError(f'Exit IP/target verification failed ({type(exc).__name__})') from None
