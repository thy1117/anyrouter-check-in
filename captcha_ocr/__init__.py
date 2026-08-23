"""Local OCR helpers for sites using base64Captcha string images."""

from .base64_captcha import CaptchaResult, solve_data_url

__all__ = ['CaptchaResult', 'solve_data_url']
