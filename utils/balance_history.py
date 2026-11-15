"""Per-account balance observations shared by serialized check-in runs."""

import base64
import hashlib
import json
import math
import os
from pathlib import Path

import httpx


class BalanceHistoryError(RuntimeError):
	"""Persistence failure with no credentials or response bodies in the message."""


def valid_balance(value):
	return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class BalanceHistory:
	def __init__(self, account, provider, *, directory=None, token=None, repository=None, client=None):
		identity = [provider.domain.rstrip('/'), account.provider, account.api_user, account.username, account.email]
		if not any(identity[2:]):
			identity.append(account.name)
		self.key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
		self.path = (
			Path(directory or os.getenv('CHECKIN_BALANCE_HISTORY_DIR', 'checkin-state/balances')) / f'{self.key}.json'
		)
		token = os.getenv('CHECKIN_STATE_GITHUB_TOKEN', '') if token is None else token
		repository = os.getenv('GITHUB_REPOSITORY', '') if repository is None else repository
		self.client = client
		if token:
			if not repository or len(repository.split('/')) != 2:
				raise BalanceHistoryError('Invalid repository for balance history')
			self.client = client or httpx.Client(
				base_url='https://api.github.com',
				headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
				timeout=20,
				trust_env=False,
				follow_redirects=False,
			)
		self.remote_path = f'/repos/{repository}/contents/checkin-state/balances/{self.key}.json'
		self.sha = None

	def _validate(self, data):
		if (
			not isinstance(data, dict)
			or data.get('version') != 1
			or not valid_balance(data.get('quota'))
			or not valid_balance(data.get('used_quota'))
			or not isinstance(data.get('observed_at'), str)
		):
			raise BalanceHistoryError('Invalid saved balance history; previous baseline was preserved')
		return data

	def load(self):
		try:
			if self.client:
				response = self.client.get(self.remote_path, params={'ref': 'main'})
				if response.status_code == 404:
					return None
				if response.status_code != 200:
					raise BalanceHistoryError(f'Cannot read balance history: HTTP {response.status_code}')
				data = response.json()
				self.sha = data['sha']
				return self._validate(json.loads(base64.b64decode(data['content'])))
			if self.path.exists():
				return self._validate(json.loads(self.path.read_text(encoding='utf-8')))
			return None
		except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
			raise BalanceHistoryError('Cannot read balance history; previous baseline was preserved') from None

	def save(self, info, observed_at):
		data = self._validate(
			{'version': 1, 'quota': info['quota'], 'used_quota': info.get('used_quota', 0), 'observed_at': observed_at}
		)
		encoded = json.dumps(data, sort_keys=True)
		try:
			if self.client:
				body = {
					'message': 'Persist observed account balance [skip ci]',
					'branch': 'main',
					'content': base64.b64encode(encoded.encode()).decode(),
				}
				if self.sha:
					body['sha'] = self.sha
				# Do not overwrite a concurrent observation or replay an ambiguous write.
				try:
					response = self.client.put(self.remote_path, json=body)
				except httpx.HTTPError:
					response = None
				if response is not None and response.status_code not in (200, 201):
					raise BalanceHistoryError(f'Cannot save balance history: HTTP {response.status_code}')
				if self.load() != data:
					raise BalanceHistoryError('Cannot confirm saved balance history; success was not recorded')
			else:
				self.path.parent.mkdir(parents=True, exist_ok=True)
				temporary = self.path.with_suffix('.pending')
				temporary.write_text(encoded + '\n', encoding='utf-8')
				os.replace(temporary, self.path)
		except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
			raise BalanceHistoryError('Cannot save balance history; success was not recorded') from None

	def close(self):
		if self.client:
			self.client.close()
