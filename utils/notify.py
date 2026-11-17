import hashlib
import os
import smtplib
import time
from email.mime.text import MIMEText
from typing import Any, Literal

import httpx


def split_telegram_message(message: str, limit: int = 4096, *, byte_limit: int = 4096) -> list[str]:
	"""Preserve text within Telegram's units and a conservative UTF-8 byte cap."""
	if limit < 2:
		raise ValueError('Telegram message budget must allow at least two UTF-16 units')
	if byte_limit < 4:
		raise ValueError('Telegram byte budget must allow a complete UTF-8 character')
	parts = []
	while message:
		units = 0
		bytes_used = 0
		end = 0
		for char in message:
			width = 2 if ord(char) > 0xFFFF else 1
			byte_width = len(char.encode('utf-8'))
			if units + width > limit or bytes_used + byte_width > byte_limit:
				break
			units += width
			bytes_used += byte_width
			end += 1
		if end < len(message):
			newline = message.rfind('\n', 0, end)
			if newline >= 0:
				end = newline + 1
		parts.append(message[:end])
		message = message[end:]
	return parts


class NotificationKit:
	def __init__(self):
		self.email_user: str = os.getenv('EMAIL_USER', '')
		self.email_pass: str = os.getenv('EMAIL_PASS', '')
		self.email_to: str = os.getenv('EMAIL_TO', '')
		self.email_sender: str = os.getenv('EMAIL_SENDER', '')
		self.smtp_server: str = os.getenv('CUSTOM_SMTP_SERVER', '')
		self.pushplus_token = os.getenv('PUSHPLUS_TOKEN')
		self.server_push_key = os.getenv('SERVERPUSHKEY')
		self.dingding_webhook = os.getenv('DINGDING_WEBHOOK')
		self.feishu_webhook = os.getenv('FEISHU_WEBHOOK')
		self.weixin_webhook = os.getenv('WEIXIN_WEBHOOK')
		self.gotify_url = os.getenv('GOTIFY_URL')
		self.gotify_token = os.getenv('GOTIFY_TOKEN')
		gotify_priority_env = os.getenv('GOTIFY_PRIORITY', '9')
		self.gotify_priority = int(gotify_priority_env) if gotify_priority_env.strip() else 9
		self.telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
		self.telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
		self.bark_key = os.getenv('BARK_KEY')
		self.bark_server = os.getenv('BARK_SERVER', 'https://api.day.app')

	def _post_json(self, service: str, url: str, data: dict[str, Any]) -> httpx.Response:
		with httpx.Client(timeout=30.0) as client:
			response = client.post(url, json=data)

		try:
			payload = response.json()
		except ValueError:
			payload = None

		if response.status_code >= 400:
			detail = ''
			if isinstance(payload, dict):
				detail = str(payload.get('description') or payload.get('errmsg') or payload.get('message') or '')
				for secret in (self.telegram_bot_token, self.telegram_chat_id):
					if secret:
						detail = detail.replace(secret, '[REDACTED]')
				detail = ' '.join(detail.split())[:500]
			suffix = f' - {detail}' if detail else ''
			raise RuntimeError(f'{service} request failed: HTTP {response.status_code}{suffix}')

		if not isinstance(payload, dict):
			return response

		error_msg = payload.get('errmsg') or payload.get('message') or payload.get('msg') or payload.get('error')
		if payload.get('ok') is False:
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("description") or "ok=false"}')
		if payload.get('errcode') not in (None, 0):
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("errcode")}')
		if payload.get('StatusCode') not in (None, 0):
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("StatusCode")}')
		if payload.get('code') not in (None, 0, 200):
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("code")}')
		if payload.get('ret') not in (None, 0, 1, 200):
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("ret")}')

		return response

	def send_email(self, title: str, content: str, msg_type: Literal['text', 'html'] = 'text'):
		if not self.email_user or not self.email_pass or not self.email_to:
			raise ValueError('Email configuration not set')

		# 如果未设置 EMAIL_SENDER，使用 EMAIL_USER 作为默认值
		sender = self.email_sender if self.email_sender else self.email_user

		# MIMEText 需要 'plain' 或 'html'，而不是 'text'
		mime_subtype = 'plain' if msg_type == 'text' else 'html'
		msg = MIMEText(content, mime_subtype, 'utf-8')
		msg['From'] = f'AnyRouter Assistant <{sender}>'
		msg['To'] = self.email_to
		msg['Subject'] = title

		smtp_server = self.smtp_server if self.smtp_server else f'smtp.{self.email_user.split("@")[1]}'
		with smtplib.SMTP_SSL(smtp_server, 465) as server:
			server.login(self.email_user, self.email_pass)
			server.send_message(msg)

	def send_pushplus(self, title: str, content: str):
		if not self.pushplus_token:
			raise ValueError('PushPlus Token not configured')

		data = {'token': self.pushplus_token, 'title': title, 'content': content, 'template': 'html'}
		self._post_json('PushPlus', 'http://www.pushplus.plus/send', data)

	def send_serverPush(self, title: str, content: str):
		if not self.server_push_key:
			raise ValueError('Server Push key not configured')

		data = {'title': title, 'desp': content}
		self._post_json('Server Push', f'https://sctapi.ftqq.com/{self.server_push_key}.send', data)

	def send_dingtalk(self, title: str, content: str):
		if not self.dingding_webhook:
			raise ValueError('DingTalk Webhook not configured')

		data = {'msgtype': 'text', 'text': {'content': f'{title}\n{content}'}}
		self._post_json('DingTalk', self.dingding_webhook, data)

	def send_feishu(self, title: str, content: str):
		if not self.feishu_webhook:
			raise ValueError('Feishu Webhook not configured')

		data = {
			'msg_type': 'interactive',
			'card': {
				'elements': [{'tag': 'markdown', 'content': content, 'text_align': 'left'}],
				'header': {'template': 'blue', 'title': {'content': title, 'tag': 'plain_text'}},
			},
		}
		self._post_json('Feishu', self.feishu_webhook, data)

	def send_wecom(self, title: str, content: str):
		if not self.weixin_webhook:
			raise ValueError('WeChat Work Webhook not configured')

		data = {'msgtype': 'text', 'text': {'content': f'{title}\n{content}'}}
		self._post_json('WeChat Work', self.weixin_webhook, data)

	def send_gotify(self, title: str, content: str):
		if not self.gotify_url or not self.gotify_token:
			raise ValueError('Gotify URL or Token not configured')

		# 使用环境变量配置的优先级，默认为9
		priority = self.gotify_priority

		# 确保优先级在有效范围内 (1-10)
		priority = max(1, min(10, priority))

		data = {'title': title, 'message': content, 'priority': priority}

		url = f'{self.gotify_url}?token={self.gotify_token}'
		self._post_json('Gotify', url, data)

	def send_telegram(self, title: str, content: str) -> list[dict[str, Any]]:
		if not self.telegram_bot_token or not self.telegram_chat_id:
			raise ValueError('Telegram Bot Token or Chat ID not configured')

		# Account names and server errors are plain text, not trusted HTML.
		full_message = f'{title}\n\n{content}'
		messages = split_telegram_message(full_message)
		if len(messages) > 1:
			# Reserve room for part numbers in both byte and character budgets.
			messages = split_telegram_message(full_message, limit=4032, byte_limit=4032)
			messages = [f'[{index}/{len(messages)}]\n{message}' for index, message in enumerate(messages, 1)]
		if any(
			len(message.encode('utf-8')) > 4096 or len(message.encode('utf-16-le')) // 2 > 4096 for message in messages
		):
			raise ValueError('Telegram part exceeds message budget; no message sent')
		receipts = []
		url = f'https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage'
		for index, message in enumerate(messages, 1):
			if index > 1:
				time.sleep(1.0)
			data = {'chat_id': self.telegram_chat_id, 'text': message}
			response = self._post_json('Telegram', url, data)
			try:
				payload = response.json()
			except ValueError as exc:
				raise RuntimeError('Telegram response is not valid JSON; delivery not confirmed') from exc
			result = payload.get('result') if isinstance(payload, dict) else None
			message_id = result.get('message_id') if isinstance(result, dict) else None
			if (
				not isinstance(payload, dict)
				or payload.get('ok') is not True
				or not isinstance(message_id, int)
				or isinstance(message_id, bool)
				or message_id <= 0
			):
				raise RuntimeError('Telegram response has no successful message receipt; delivery not confirmed')
			receipt = {
				'part': index,
				'total_parts': len(messages),
				'message_id': message_id,
				'utf8_bytes': len(message.encode('utf-8')),
				'utf16_units': len(message.encode('utf-16-le')) // 2,
				'sha256': hashlib.sha256(message.encode('utf-8')).hexdigest(),
			}
			receipts.append(receipt)
			chat_tail = str(self.telegram_chat_id)[-4:]
			print(
				f'[Telegram] part {index}/{len(messages)}, message_id: {message_id}, chat_id tail: {chat_tail}, '
				f'utf8_bytes: {receipt["utf8_bytes"]}, utf16_units: {receipt["utf16_units"]}, sha256: {receipt["sha256"]}'
			)
		return receipts

	def send_bark(self, title: str, content: str):
		if not self.bark_key:
			raise ValueError('Bark Key not configured')

		# Bark API 支持 GET/POST，这里使用 POST JSON 方式支持更多参数
		# 文档: https://bark.day.app/#/tutorial
		url = f'{self.bark_server.rstrip("/")}/push'
		data = {
			'device_key': self.bark_key,
			'title': title,
			'body': content,
			'icon': 'https://anyrouter.top/favicon.ico',  # 可选：尝试使用 AnyRouter 图标
			'group': 'AnyRouter',
		}

		self._post_json('Bark', url, data)

	def push_message(self, title: str, content: str, msg_type: Literal['text', 'html'] = 'text') -> dict[str, bool]:
		notifications = [
			('Email', lambda: self.send_email(title, content, msg_type)),
			('PushPlus', lambda: self.send_pushplus(title, content)),
			('Server Push', lambda: self.send_serverPush(title, content)),
			('DingTalk', lambda: self.send_dingtalk(title, content)),
			('Feishu', lambda: self.send_feishu(title, content)),
			('WeChat Work', lambda: self.send_wecom(title, content)),
			('Gotify', lambda: self.send_gotify(title, content)),
			('Telegram', lambda: self.send_telegram(title, content)),
			('Bark', lambda: self.send_bark(title, content)),
		]

		results = {}
		for name, func in notifications:
			try:
				func()
				results[name] = True
				print(f'[{name}]: Message push successful!')
			except Exception as e:
				results[name] = False
				error = str(e)
				for secret in (self.telegram_bot_token, self.telegram_chat_id):
					if secret:
						error = error.replace(secret, '[REDACTED]')
				print(f'[{name}]: Message push failed! Reason: {error}')
		return results


notify = NotificationKit()
