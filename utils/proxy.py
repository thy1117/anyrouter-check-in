"""代理配置：读取环境变量并供浏览器 / HTTP 客户端使用。"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Generator

from utils.debug import debug_print


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
		debug_print(f'[WARN] {prefix}Failed to switch proxy node to "{node_name}"')

	try:
		yield
	finally:
		if switched and previous_node and previous_node != node_name:
			restored = switch_mihomo_node(previous_node)
			if restored:
				print(f'[INFO] {prefix}Restored proxy node to "{previous_node}"')
			else:
				debug_print(f'[WARN] {prefix}Failed to restore proxy node to "{previous_node}"')
