"""Match a slider piece against its shaded position in the background."""

import base64
import io

import numpy as np
from PIL import Image


def _decode(value: str, image_format: str, size: tuple[int, int]) -> np.ndarray:
	prefix = f'data:image/{image_format.lower()};base64,'
	if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 1_500_000:
		raise ValueError('Invalid slider image')
	try:
		with Image.open(io.BytesIO(base64.b64decode(value[len(prefix) :], validate=True))) as image:
			if image.format != image_format or image.size != size:
				raise ValueError
			return np.asarray(image.convert('RGBA'), dtype=np.float64)
	except (ValueError, OSError, Image.DecompressionBombError):
		raise ValueError('Invalid slider image dimensions or data') from None


def solve_challenge(challenge: dict) -> int:
	"""Return a confident native-image x coordinate, rejecting ambiguous matches."""
	if not isinstance(challenge, dict):
		raise ValueError('Invalid slider challenge')
	width, height, size, y = (challenge.get(key) for key in ('width', 'height', 'piece_size', 'y'))
	if (
		any(type(value) is not int for value in (width, height, size, y))
		or not 8 <= size < min(width, height) <= max(width, height) <= 1024
		or not 0 <= y <= height - size
	):
		raise ValueError('Invalid slider geometry')
	background = _decode(challenge.get('background'), 'JPEG', (width, height))
	piece = _decode(challenge.get('piece'), 'PNG', (size, size))
	mask = piece[:, :, 3] >= 250
	mask[:2, :] = mask[-2:, :] = False
	mask[:, :2] = mask[:, -2:] = False
	interior = mask.copy()
	for dy, dx in ((-2, 0), (2, 0), (0, -2), (0, 2)):
		interior &= np.roll(mask, (dy, dx), (0, 1))
	if interior.sum() < 100:
		raise ValueError('Slider piece has insufficient opaque pixels')
	reference = piece[:, :, :3][interior].ravel()
	reference -= reference.mean()
	reference_norm = np.linalg.norm(reference)
	if reference_norm < 1:
		raise ValueError('Slider piece has insufficient detail')
	scores = []
	for x in range(width - size + 1):
		candidate = background[y : y + size, x : x + size, :3][interior].ravel()
		candidate -= candidate.mean()
		scores.append(float(np.dot(reference, candidate) / (reference_norm * np.linalg.norm(candidate) + 1e-9)))
	best = int(np.argmax(scores))
	alternatives = [score for x, score in enumerate(scores) if abs(x - best) > 5]
	if scores[best] < 0.85 or not alternatives or scores[best] - max(alternatives) < 0.04:
		# Highlighted, low-texture pieces can lose their original colour correlation.
		outline = np.stack(np.gradient((piece[:, :, 3] >= 200).astype(np.float64)))
		gradient = np.stack(np.gradient(background[:, :, :3].mean(axis=2)))
		outline_norm = np.linalg.norm(outline)
		scores = []
		for x in range(width - size + 1):
			candidate = gradient[:, y : y + size, x : x + size]
			scores.append(float(-np.sum(outline * candidate) / (outline_norm * np.linalg.norm(candidate) + 1e-9)))
		best = int(np.argmax(scores))
		alternatives = [score for x, score in enumerate(scores) if abs(x - best) > 5]
		if scores[best] < 0.45 or not alternatives or scores[best] - max(alternatives) < 0.1:
			raise ValueError('Slider match is ambiguous; no verification submitted')
	return best
