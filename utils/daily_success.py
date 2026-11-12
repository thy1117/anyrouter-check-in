"""Confirmed daily outcomes, shared across serialized Actions dispatches."""

import base64
import binascii
import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import httpx

STATE_PATH = 'checkin-state/daily-success.json'
MISSING_STATE = object()


class DailyStateError(RuntimeError):
	"""Safe-to-log persistence failure; contains no credential or response body."""


class DailySuccessState:
	def __init__(self, day: str, path: str, *, token='', repository='', client=None):
		self.day = day
		self.path = Path(path)
		self.confirmed: set[str] = set()
		self.dirty = False
		self.sha = None
		self.client = client
		self.remote_path = f'/repos/{repository}/contents/{STATE_PATH}'
		if token:
			if not repository or len(repository.split('/')) != 2:
				raise DailyStateError('Invalid repository for daily success state')
			self.client = client or httpx.Client(
				base_url='https://api.github.com',
				headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
				timeout=20,
				trust_env=False,
				follow_redirects=False,
			)
		if self.client:
			self.sha, state = self._read_remote()
		elif self.path.exists():
			try:
				state = json.loads(self.path.read_text())
			except (OSError, ValueError):
				raise DailyStateError('Cannot read daily success state; refusing a full replay') from None
		else:
			state = MISSING_STATE
		self.confirmed = self._keys_for_day(state)

	def _keys_for_day(self, state):
		if state is MISSING_STATE:
			return set()
		if state is None:
			raise DailyStateError('Invalid daily success state; refusing a full replay')
		if (
			not isinstance(state, dict)
			or state.get('version') != 1
			or not isinstance(state.get('day'), str)
			or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', state['day'])
			or not self._is_calendar_day(state['day'])
			or not isinstance(state.get('confirmed'), list)
			or not all(isinstance(k, str) and re.fullmatch(r'[0-9a-f]{64}', k) for k in state['confirmed'])
		):
			raise DailyStateError('Invalid daily success state; refusing a full replay')
		if state['day'] > self.day:
			raise DailyStateError('Daily state is from a newer day; refusing to overwrite it')
		return set(state['confirmed']) if state['day'] == self.day else set()

	@staticmethod
	def _is_calendar_day(value: str) -> bool:
		try:
			date.fromisoformat(value)
		except ValueError:
			return False
		return True

	def _read_remote(self):
		try:
			response = self.client.get(self.remote_path, params={'ref': 'main'})
			if response.status_code == 404:
				return None, MISSING_STATE
			if response.status_code != 200:
				raise DailyStateError(f'Cannot read daily success state: HTTP {response.status_code}')
			data = response.json()
			state = json.loads(base64.b64decode(data['content'], validate=False))
			self._keys_for_day(state)
			return data['sha'], state
		except (httpx.HTTPError, ValueError, KeyError, TypeError, binascii.Error):
			raise DailyStateError('Cannot read daily success state; refusing a full replay') from None

	@staticmethod
	def account_key(account, index):
		identity = [
			account.provider,
			account.get_display_name(index),
			account.api_user,
			account.username,
			account.email,
		]
		return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()

	def contains(self, account, index):
		return self.account_key(account, index) in self.confirmed

	def mark_success(self, account, index):
		self.confirmed.add(self.account_key(account, index))
		self.dirty = True
		self._save_local()

	def _payload(self):
		return {'version': 1, 'day': self.day, 'confirmed': sorted(self.confirmed)}

	def _save_local(self):
		temporary = self.path.with_suffix(self.path.suffix + '.pending')
		try:
			self.path.parent.mkdir(parents=True, exist_ok=True)
			temporary.write_text(json.dumps(self._payload(), sort_keys=True) + '\n')
			os.replace(temporary, self.path)
		except OSError:
			raise DailyStateError('Cannot save daily success checkpoint') from None

	def save(self):
		if not self.dirty or not self.client:
			return
		last_error = 'unknown persistence error'
		for attempt in range(3):
			# Merge the latest same-day state before each optimistic-concurrency write.
			self.sha, remote = self._read_remote()
			remote_keys = self._keys_for_day(remote)
			if isinstance(remote, dict) and remote.get('day', '') > self.day:
				raise DailyStateError('Daily state advanced to a newer day; refusing to overwrite it')
			self.confirmed.update(remote_keys)
			payload = self._payload()
			body = {
				'message': 'Persist confirmed daily check-ins [skip ci]',
				'branch': 'main',
				'content': base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode(),
			}
			if self.sha:
				body['sha'] = self.sha
			try:
				response = self.client.put(self.remote_path, json=body)
			except httpx.HTTPError as exc:
				response = None
				last_error = f'{type(exc).__name__}'
			else:
				last_error = f'HTTP {response.status_code}'

			# A timeout or conflict may have committed/changed the file. Read back
			# before retrying, and never overwrite a newer day.
			sha, remote = self._read_remote()
			remote_keys = self._keys_for_day(remote)
			if isinstance(remote, dict) and remote.get('day', '') > self.day:
				raise DailyStateError('Daily state advanced to a newer day; refusing to overwrite it')
			if isinstance(remote, dict) and remote.get('day') == self.day:
				if self.confirmed <= remote_keys:
					self.sha = sha
					self.confirmed.update(remote_keys)
					self.dirty = False
					return
				self.confirmed.update(remote_keys)
				self.sha = sha
			if response is not None and response.status_code not in (200, 201, 409, 422) and response.status_code < 500:
				raise DailyStateError(f'Cannot save daily success state: HTTP {response.status_code}')
			self._save_local()
			if attempt < 2:
				continue
			raise DailyStateError(f'Cannot confirm saved daily success state: {last_error}')

	def close(self):
		if self.client:
			self.client.close()


def create_daily_success_state(day):
	path = os.getenv('CHECKIN_DAILY_STATE_FILE', '').strip()
	if not path:
		return None
	return DailySuccessState(
		day,
		path,
		token=os.getenv('CHECKIN_STATE_GITHUB_TOKEN', ''),
		repository=os.getenv('GITHUB_REPOSITORY', ''),
	)
