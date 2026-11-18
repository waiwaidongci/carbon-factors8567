from decimal import Decimal
from src.services.matching import solve


def draft(id, period, department, quantity, unit='kWh', activity='电力', revision=0):
    kwh = Decimal(str(quantity)) * (Decimal('1000') if unit == 'MWh' else Decimal('1'))
    return {
        'id': id,
        'natural_key': f'demo-{id}',
        'revision': revision,
        'data': {'period': period, 'department': department, 'activity': activity,
                 'quantity': quantity, 'unit': unit},
        'kwh': kwh,
    }


def line(id, period, department, target, unit='kWh', quantity=None):
    return {
        'id': id, 'position': id, 'period': period, 'department': department,
        'quantity': quantity if quantity is not None else target, 'unit': unit,
        'target_kwh': Decimal(str(target)),
    }


def test_merges_multiple_drafts_into_one_line():
    drafts = [draft(1, '2026-11', '材料', 100), draft(2, '2026-11', '材料', 250), draft(3, '2026-11', '材料', 700)]
    result = solve([line(10, '2026-11', '材料', 350)], drafts)
    assert result['status'] == 'unique'
    assert sorted(item['source_id'] for item in result['matches'][10]) == [1, 2]


def test_unit_conversion_combines_kwh_and_mwh():
    drafts = [draft(1, '2026-11', '材料', 0.25, 'MWh'), draft(2, '2026-11', '材料', 750, 'kWh')]
    result = solve([line(1, '2026-11', '材料', 1000)], drafts)
    assert result['status'] == 'unique'
    assert sorted(item['source_id'] for item in result['matches'][1]) == [1, 2]


def test_partial_kwh_target_matches_mwh_line():
    drafts = [draft(1, '2026-11', '材料', 1.5, 'MWh')]
    result = solve([line(1, '2026-11', '材料', 1500)], drafts)
    assert result['status'] == 'unique'


def test_no_solution_when_total_is_short():
    drafts = [draft(1, '2026-11', '材料', 100), draft(2, '2026-11', '材料', 200)]
    result = solve([line(1, '2026-11', '材料', 400)], drafts)
    assert result['status'] == 'no_solution'
    report = result['line_reports'][0]
    assert '不足目标' in report['reason']
    assert result['matches'] == {}


def test_no_solution_when_amount_not_exactly_reachable():
    drafts = [draft(1, '2026-11', '材料', 100), draft(2, '2026-11', '材料', 300)]
    result = solve([line(1, '2026-11', '材料', 250)], drafts)
    assert result['status'] == 'no_solution'
    assert '没有任何组合精确等于' in result['line_reports'][0]['reason']


def test_ambiguous_when_two_disjoint_combinations_exist():
    drafts = [draft(1, '2026-11', '材料', 100), draft(2, '2026-11', '材料', 100),
              draft(3, '2026-11', '材料', 200)]
    result = solve([line(1, '2026-11', '材料', 100)], drafts)
    assert result['status'] == 'ambiguous'
    assert len(result['solutions']) == 2
    assert result['matches'] == {}


def test_unique_across_two_lines():
    drafts = [draft(1, '2026-11', '材料', 100), draft(2, '2026-11', '材料', 200),
              draft(3, '2026-11', '材料', 400), draft(4, '2026-11', '材料', 20),
              draft(5, '2026-11', '材料', 30)]
    lines = [line(1, '2026-11', '材料', 300), line(2, '2026-11', '材料', 420)]
    result = solve(lines, drafts)
    assert result['status'] == 'unique'
    assert sorted(item['source_id'] for item in result['matches'][1]) == [1, 2]
    assert sorted(item['source_id'] for item in result['matches'][2]) == [3, 4]


def test_cross_line_duplicate_is_no_solution():
    drafts = [draft(1, '2026-11', '材料', 100), draft(2, '2026-11', '材料', 200)]
    lines = [line(1, '2026-11', '材料', 100), line(2, '2026-11', '材料', 300)]
    result = solve(lines, drafts)
    assert result['status'] == 'no_solution'
    assert '重复使用' in result['explanation']


def test_claimed_drafts_are_excluded():
    drafts = [draft(1, '2026-11', '材料', 100), draft(2, '2026-11', '材料', 100)]
    result = solve([line(1, '2026-11', '材料', 100)], drafts, claimed_ids={1})
    assert result['status'] == 'unique'
    assert [item['source_id'] for item in result['matches'][1]] == [2]
    assert result['line_reports'][0]['claimed_count'] == 1


def test_non_electricity_drafts_are_ignored():
    drafts = [draft(1, '2026-11', '材料', 100, activity='柴油')]
    result = solve([line(1, '2026-11', '材料', 100)], drafts)
    assert result['status'] == 'no_solution'
    assert '没有可用的电力底稿' in result['line_reports'][0]['reason']


def test_department_and_period_scoped():
    drafts = [draft(1, '2026-11', '材料', 100), draft(2, '2026-10', '材料', 100), draft(3, '2026-11', '工艺', 100)]
    result = solve([line(1, '2026-11', '材料', 100)], drafts)
    assert result['status'] == 'unique'
    assert [item['source_id'] for item in result['matches'][1]] == [1]
