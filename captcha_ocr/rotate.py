"""Match NexaVlinks' rotated center against its unrotated reference image."""

import base64
import io

import numpy as np
from PIL import Image


def _decode(value: str, size: tuple[int, int]) -> Image.Image:
	prefix = 'data:image/png;base64,'
	if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 1_500_000:
		raise ValueError('Invalid rotation image')
	try:
		image = Image.open(io.BytesIO(base64.b64decode(value[len(prefix) :], validate=True)))
		if image.format != 'PNG' or image.size != size:
			raise ValueError
		return image.convert('RGBA')
	except (ValueError, OSError):
		raise ValueError('Invalid rotation image dimensions or PNG data') from None


def solve_data_urls(image: str, thumb: str) -> int:
	"""Return clockwise degrees; reject ambiguous matches without server guesses."""
	# ponytail: fixed current image sizes; reject changed layouts instead of guessing.
	background = _decode(image, (220, 220))
	center = _decode(thumb, (140, 140))
	reference = np.asarray(background.crop((40, 40, 180, 180)), dtype=np.float32)
	y, x = np.mgrid[:140, :140]
	interior = (x - 69.5) ** 2 + (y - 69.5) ** 2 < 63**2
	scores = []
	for clockwise in range(360):
		pixels = np.asarray(center.rotate(-clockwise, resample=Image.Resampling.BICUBIC), dtype=np.float32)
		mask = interior & (pixels[..., 3] >= 250) & (reference[..., 3] >= 250)
		if int(mask.sum()) < 5_000:
			raise ValueError('Rotation image has insufficient opaque pixels')
		scores.append(float(np.mean((pixels[..., :3][mask] - reference[..., :3][mask]) ** 2)))

	errors = np.asarray(scores)
	best = int(errors.argmin())
	separation = np.abs((np.arange(360) - best + 180) % 360 - 180)
	alternative = float(errors[separation >= 10].min())
	if alternative / max(float(errors[best]), 1e-6) < 3:
		raise ValueError('Rotation match is ambiguous; no check-in submitted')
	return best
