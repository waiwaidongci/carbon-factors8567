from decimal import Decimal
from collections import defaultdict

ELECTRICITY_ACTIVITY = '电力'
ELECTRICITY_UNITS = {'kWh': Decimal('1'), 'MWh': Decimal('1000')}

MAX_LINE_CANDIDATES = 5000
MAX_LINE_SEARCH_STEPS = 500_000
MAX_TOTAL_LINE_STEPS = 1_000_000
MAX_SEARCH_STEPS = 500_000
MAX_SAMPLE_SUBSETS = 5


def decimal_value(value):
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def to_kwh(quantity, unit):
    factor = ELECTRICITY_UNITS.get(unit)
    number = decimal_value(quantity)
    if factor is None or number is None:
        return None
    return number * factor


def dec_str(value):
    """规范化十进制文本：去掉尾随零与科学计数法，便于跨单位精确比较与展示。"""
    text = format(value.normalize(), 'f')
    return text if text != '-0' else '0'


def draft_brief(draft):
    return {
        'source_id': draft['id'],
        'natural_key': draft.get('natural_key'),
        'quantity': draft['data'].get('quantity'),
        'unit': draft['data'].get('unit'),
        'quantity_kwh': dec_str(draft['kwh']),
        'period': draft['data'].get('period'),
        'department': draft['data'].get('department'),
    }


def _subset_sums(items, target, cap, budget):
    """枚举和精确等于 target 的底稿子集；超过 cap 或步数上限时标记截断。"""
    # 电量超过目标的单条底稿不可能参与正数组合，先剪枝。
    ordered = sorted((item for item in items if item['kwh'] <= target),
                     key=lambda item: item['kwh'], reverse=True)
    suffix = [Decimal('0')] * (len(ordered) + 1)
    for index in range(len(ordered) - 1, -1, -1):
        suffix[index] = suffix[index + 1] + ordered[index]['kwh']
    results = []
    state = {'aborted': False, 'steps': 0}

    def walk(index, remaining, chosen):
        state['steps'] += 1
        budget['steps'] += 1
        if state['aborted']:
            return
        if state['steps'] > MAX_LINE_SEARCH_STEPS or budget['steps'] > MAX_TOTAL_LINE_STEPS:
            state['aborted'] = True
            return
        if remaining == 0:
            results.append(tuple(chosen))
            if len(results) >= cap:
                state['aborted'] = True
            return
        if index >= len(ordered) or remaining < 0 or suffix[index] < remaining:
            return
        item = ordered[index]
        if item['kwh'] <= remaining:
            chosen.append(item)
            walk(index + 1, remaining - item['kwh'], chosen)
            chosen.pop()
            if state['aborted']:
                return
        walk(index + 1, remaining, chosen)

    walk(0, target, [])
    return results, state['aborted']


def solve(lines, drafts, claimed_ids=()):
    """整单求解：同月份+部门内选互不重叠的电力底稿，统一 kWh 后逐行精确相等。

    lines: [{id, position, period, department, quantity, unit, target_kwh(Decimal)}]
    drafts: [{id, natural_key, revision, data, kwh(Decimal)}]
    claimed_ids: 已被其他账单认领的底稿 id 集合
    """
    claimed_ids = set(claimed_ids)
    groups = defaultdict(list)
    claimed_in_group = defaultdict(int)
    for draft in drafts:
        if draft['data'].get('activity') != ELECTRICITY_ACTIVITY:
            continue
        key = (draft['data'].get('period'), draft['data'].get('department'))
        if draft['id'] in claimed_ids:
            claimed_in_group[key] += 1
            continue
        if draft['kwh'] <= 0:
            continue
        groups[key].append(draft)

    line_reports = []
    candidate_sets = []
    all_rows_have_candidates = True
    truncated = False
    budget = {'steps': 0}
    for line in lines:
        key = (line['period'], line['department'])
        candidates = groups.get(key, [])
        target = line['target_kwh']
        subsets, aborted = _subset_sums(candidates, target, MAX_LINE_CANDIDATES, budget)
        truncated = truncated or aborted
        report = {
            'line_id': line['id'],
            'position': line['position'],
            'period': line['period'],
            'department': line['department'],
            'quantity': line.get('quantity'),
            'unit': line.get('unit'),
            'target_kwh': dec_str(target),
            'available_count': len(candidates),
            'claimed_count': claimed_in_group.get(key, 0),
            'available_total_kwh': dec_str(sum((d['kwh'] for d in candidates), Decimal('0'))),
            'candidate_count': len(subsets),
            'candidate_samples': [[draft_brief(d) for d in subset] for subset in subsets[:MAX_SAMPLE_SUBSETS]],
            'reason': '',
        }
        if not subsets:
            all_rows_have_candidates = False
            if report['claimed_count'] and not candidates:
                report['reason'] = '同月同部门的电力底稿均已被其他账单认领，没有可用底稿。'
            elif not candidates:
                report['reason'] = '同月同部门没有可用的电力底稿。'
            elif Decimal(report['available_total_kwh']) < target:
                gap = target - Decimal(report['available_total_kwh'])
                report['reason'] = f'可用电力底稿合计 {report["available_total_kwh"]} kWh，不足目标 {dec_str(target)} kWh（差 {dec_str(gap)} kWh）。'
            else:
                report['reason'] = f'候选电力底稿存在，但没有任何组合精确等于目标 {dec_str(target)} kWh。'
        line_reports.append(report)
        candidate_sets.append(subsets)

    base = {'line_reports': line_reports, 'truncated': truncated, 'solutions': []}
    if not all_rows_have_candidates:
        return {**base, 'status': 'no_solution', 'matches': {},
                'explanation': '至少一行无法用互不重叠的既有底稿精确对平，整单不认领任何底稿。'}

    if truncated:
        return {**base, 'status': 'ambiguous', 'matches': {},
                'explanation': '候选组合数量超出可穷尽核验范围，无法确认整单唯一解，按争议处理且不认领。'}

    order = sorted(range(len(lines)), key=lambda index: len(candidate_sets[index]))
    used = set()
    path = {}
    solutions = []
    search = {'steps': 0, 'aborted': False}

    def walk(depth):
        search['steps'] += 1
        if search['aborted'] or len(solutions) >= 2:
            return
        if search['steps'] > MAX_SEARCH_STEPS:
            search['aborted'] = True
            return
        if depth == len(order):
            solutions.append({line['id']: [draft_brief(d) for d in path[line['id']]] for line in lines})
            return
        line_index = order[depth]
        line = lines[line_index]
        for subset in candidate_sets[line_index]:
            ids = {d['id'] for d in subset}
            if ids & used:
                continue
            used.update(ids)
            path[line['id']] = subset
            walk(depth + 1)
            used.difference_update(ids)
            path.pop(line['id'], None)
            if search['aborted'] or len(solutions) >= 2:
                return

    walk(0)
    if search['aborted']:
        return {**base, 'status': 'ambiguous', 'matches': {},
                'explanation': '整单候选组合的交叉核验超出上限，无法确认唯一解，按争议处理且不认领。'}

    if not solutions:
        multi = [r for r in line_reports if r['candidate_count'] > 1]
        detail = ''
        if multi:
            detail = '；存在多套候选的行：' + '、'.join(f'第{r["position"]}行' for r in multi[:5])
        return {**base, 'status': 'no_solution', 'matches': {},
                'explanation': '各行均有候选底稿，但组合之间会重复使用同一条底稿，无法整单互不重叠地同时对平' + detail + '。'}

    if len(solutions) >= 2:
        multi = [r for r in line_reports if r['candidate_count'] > 1]
        detail = '；存在多套候选的行：' + '、'.join(f'第{r["position"]}行' for r in multi[:5]) if multi else ''
        return {**base, 'status': 'ambiguous', 'matches': {}, 'solutions': solutions[:2],
                'explanation': '至少存在两套互不重叠的认领方案，无法确定唯一底稿组合，按争议处理且不认领' + detail + '。'}

    matches = dict(solutions[0])
    return {**base, 'status': 'unique', 'matches': matches, 'solutions': solutions,
            'explanation': '全单唯一解：每个账单行都有且只有一套互不重叠的底稿组合，已整单认领。'}
