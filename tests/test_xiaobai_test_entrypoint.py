import json

import pytest

from scripts.check_xiaobai import select_account


def test_exact_name_selects_only_one_xiaobai():
	slots = {
		'one': json.dumps(
			[
				{'provider': 'xiaobai', 'name': 'target', 'access_token': 'a'},
				{'provider': 'xiaobai', 'name': 'other', 'access_token': 'b'},
				{'provider': 'anyrouter', 'name': 'target', 'access_token': 'c'},
			]
		)
	}
	result = select_account('target', slots)
	assert result.provider == 'xiaobai'
	assert result.access_token == 'a'


@pytest.mark.parametrize(
	'slots',
	[
		{'one': '[]'},
		{'one': json.dumps([{'provider': 'xiaobai', 'name': 'target'}] * 2)},
		{'one': json.dumps({'provider': 'xiaobai', 'name': 'target'})},
	],
)
def test_missing_ambiguous_or_malformed_input_fails_closed(slots):
	with pytest.raises(ValueError):
		select_account('target', slots)
