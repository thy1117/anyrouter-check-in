"""Encrypted Xiaobai refresh-token state, separate from code and account Secrets.

Only the short-lived, repository-scoped GITHUB_TOKEN is used for writes. A
compare-and-swap pending marker is committed BEFORE rotating a refresh token.
An interrupted rotation therefore fails closed instead of reusing the old token.
"""

import base64
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken

STATE_BRANCH = 'checkin-token-state'
RECOVERY_DIR = Path('.xiaobai-token-recovery')


class TokenStateError(RuntimeError):
	"""Safe-to-log state error. Never include response bodies or token values."""


def mask_tokens(*tokens: str | None) -> None:
	if os.getenv('GITHUB_ACTIONS') == 'true':
		for token in tokens:
			if token:
				value = token.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
				print(f'::add-mask::{value}', flush=True)


class XiaobaiTokenState:
	def __init__(self, account, domain: str, key: str, github_token: str, repository: str, *, client=None):
		if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
			raise TokenStateError('Invalid GITHUB_REPOSITORY for encrypted token state')
		try:
			self.cipher = Fernet(key.encode())
		except (ValueError, TypeError):
			raise TokenStateError('XIAOBAI_TOKEN_STATE_KEY is not a valid Fernet key') from None
		seed_tokens = [account.access_token or '', account.refresh_token or '']
		self.seed = hashlib.sha256(json.dumps(seed_tokens).encode()).hexdigest()
		identity = [domain.rstrip('/'), account.provider, account.name or account.email or self.seed]
		self.scope = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
		self.path = f'/repos/{repository}/contents/xiaobai/{self.scope}.fernet'
		self.bootstrap = tuple(seed_tokens)
		self.sha = None
		self.client = client or httpx.Client(
			base_url='https://api.github.com',
			headers={'Authorization': f'Bearer {github_token}', 'Accept': 'application/vnd.github+json'},
			trust_env=False,
			timeout=20.0,
			follow_redirects=False,
		)

	def close(self):
		self.client.close()

	def _read(self):
		try:
			response = self.client.get(self.path, params={'ref': STATE_BRANCH})
		except httpx.HTTPError:
			raise TokenStateError('Cannot read encrypted token state; no refresh was attempted') from None
		if response.status_code == 404:
			return None, None
		if response.status_code != 200:
			raise TokenStateError(f'Cannot read encrypted token state: HTTP {response.status_code}')
		try:
			value = response.json()
			return value['sha'], base64.b64decode(value['content'])
		except (ValueError, KeyError, TypeError):
			raise TokenStateError('Invalid encrypted token state response') from None

	def load(self) -> tuple[str, str]:
		self.sha, ciphertext = self._read()
		if ciphertext is None:
			return self.bootstrap
		try:
			state = json.loads(self.cipher.decrypt(ciphertext))
			if state['version'] != 1 or state['scope'] != self.scope:
				raise ValueError
			if state['seed'] != self.seed:
				# A deliberate Secret update supersedes the saved session, including
				# an interrupted refresh. Never let stale saved tokens override it.
				return self.bootstrap
			if state['pending']:
				raise TokenStateError(
					'Previous token refresh was interrupted; recover the encrypted checkpoint '
					"or update this account's login tokens before retrying"
				)
			access, refresh = state['tokens']
			if not isinstance(access, str) or not isinstance(refresh, str):
				raise ValueError
		except (InvalidToken, ValueError, KeyError, TypeError):
			raise TokenStateError('Cannot authenticate/decode token state; refusing to reuse stale tokens') from None
		mask_tokens(access, refresh)
		return access, refresh

	def _checkpoint(self, ciphertext: bytes) -> Path:
		RECOVERY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
		os.chmod(RECOVERY_DIR, 0o700)
		destination = RECOVERY_DIR / f'{self.scope}.fernet'
		fd, temporary = tempfile.mkstemp(dir=RECOVERY_DIR, prefix='.pending-')
		try:
			with os.fdopen(fd, 'wb') as output:
				output.write(ciphertext)
				output.flush()
				os.fsync(output.fileno())
			os.replace(temporary, destination)
		finally:
			Path(temporary).unlink(missing_ok=True)
		return destination

	def save(self, access: str, refresh: str, *, pending: bool = False) -> None:
		mask_tokens(access, refresh)
		state = {
			'version': 1,
			'scope': self.scope,
			'seed': self.seed,
			'tokens': [access, refresh],
			'pending': pending,
		}
		ciphertext = self.cipher.encrypt(json.dumps(state).encode())
		checkpoint = self._checkpoint(ciphertext)
		body = {
			'message': 'Persist encrypted Xiaobai authentication state [skip ci]',
			'branch': STATE_BRANCH,
			'content': base64.b64encode(ciphertext).decode(),
		}
		if self.sha:
			body['sha'] = self.sha
		for attempt in range(3):
			try:
				response = self.client.put(self.path, json=body)
			except httpx.HTTPError:
				response = None
			if response is not None and response.status_code in (200, 201):
				try:
					self.sha = response.json()['content']['sha']
				except (ValueError, KeyError, TypeError):
					pass  # Resolve an ambiguous acknowledgement with a read below.
				else:
					checkpoint.unlink(missing_ok=True)
					return
			# A timed-out PUT might already have committed. Reconcile rather than
			# overwriting a concurrent writer or rotating the token again.
			remote_sha, remote_ciphertext = self._read()
			if remote_ciphertext == ciphertext:
				self.sha = remote_sha
				checkpoint.unlink(missing_ok=True)
				return
			if remote_sha != self.sha:
				raise TokenStateError('Concurrent token-state update detected; refusing to overwrite it')
			if response is not None and response.status_code < 500:
				raise TokenStateError(
					f'Cannot save encrypted token state: HTTP {response.status_code}; '
					'check contents:write and the checkin-token-state branch'
				)
			if attempt < 2:
				time.sleep(attempt + 1)
		raise TokenStateError('Token-state save failed; encrypted recovery checkpoint retained')


def create_xiaobai_token_state(account, domain: str) -> XiaobaiTokenState | None:
	key = os.getenv('XIAOBAI_TOKEN_STATE_KEY', '').strip()
	github_token = os.getenv('XIAOBAI_STATE_GITHUB_TOKEN', '').strip()
	if not key and not github_token:
		return None
	if not key or not github_token:
		raise TokenStateError('Both XIAOBAI_TOKEN_STATE_KEY and XIAOBAI_STATE_GITHUB_TOKEN are required')
	return XiaobaiTokenState(account, domain, key, github_token, os.getenv('GITHUB_REPOSITORY', ''))
