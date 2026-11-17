"""Keep provider account groups together in check-in notifications."""

from collections import Counter

from utils.config import AccountConfig

SUCCESS_HEADING = '✅ 独立签到成功'
BALANCE_HEADING = '💰 余额明细'


def _append_provider_groups(lines: list[str], rows: list[tuple[str, str]]) -> None:
	groups: dict[str, list[str]] = {}
	for provider, row in rows:
		groups.setdefault(provider, []).append(row)
	for index, group in enumerate(groups.values()):
		if index:
			lines.append('')
		lines.extend(group)


def group_account_report(content: str, accounts: list[AccountConfig]) -> str:
	"""Place sites with one configured account after all multi-account groups.

	Only account detail sections are rearranged. Row text and the failure summary
	are retained, and a missing balance never changes a site's account count.
	"""
	lines = content.split('\n')
	headings = (SUCCESS_HEADING, BALANCE_HEADING)
	start = next((index for index, line in enumerate(lines) if line in headings), None)
	if start is None or not accounts:
		return content
	end = next((index for index in range(start, len(lines)) if lines[index].startswith('[SCREENSHOT]')), len(lines))
	providers = {account.get_display_name(index): account.provider for index, account in enumerate(accounts)}
	counts = Counter(account.provider for account in accounts)
	sections: dict[str, list[tuple[str, str]]] = {heading: [] for heading in headings}
	section = SUCCESS_HEADING
	for line in lines[start:end]:
		if line in headings:
			section = line
		elif line.strip():
			if not line.startswith(('✅ ', '❌ ')):
				return content
			name = line[2:].split('｜', 1)[0].split(' · ', 1)[0]
			if name not in providers:
				return content
			sections[section].append((providers[name], line))

	singles = []
	for heading, rows in sections.items():
		singles.extend(row for row in rows if counts[row[0]] == 1)
		sections[heading] = [row for row in rows if counts[row[0]] > 1]

	result = lines[:start]
	while result and not result[-1]:
		result.pop()
	if sections[SUCCESS_HEADING]:
		result.extend(['', SUCCESS_HEADING])
		_append_provider_groups(result, sections[SUCCESS_HEADING])
	if sections[BALANCE_HEADING] or singles:
		result.extend(['', BALANCE_HEADING])
		_append_provider_groups(result, sections[BALANCE_HEADING])
		if sections[BALANCE_HEADING] and singles:
			result.append('')
		result.extend(row for _, row in sorted(singles, key=lambda item: item[0]))
	if end < len(lines):
		result.extend(['', *lines[end:]])
	return '\n'.join(result)
