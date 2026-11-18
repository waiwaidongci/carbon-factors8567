import threading

import pytest
from fastapi.testclient import TestClient

from src.config.database import Base, engine, Session
from src.config.settings import CONFIG
from src.main import app
from src.models.identity import User
from src.models.source import Source, Policy
from src.services import bill as bill_service
from src.services import ledger
from src.utils.security import password_hash


@pytest.fixture(scope='module')
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=role + '@demo.local', role=role, password=password_hash('Demo2026!')))
        db.add(Policy(version=CONFIG['policy']['version'], data=CONFIG['policy']))
        # 电力底稿：材料试验室 2026-11 合计可用于 300 kWh
        drafts = [
            ('e1', {'period': '2026-11', 'department': '材料试验室', 'activity': '电力', 'quantity': 100, 'unit': 'kWh'}),
            ('e2', {'period': '2026-11', 'department': '材料试验室', 'activity': '电力', 'quantity': 200, 'unit': 'kWh'}),
            ('e3', {'period': '2026-11', 'department': '材料试验室', 'activity': '电力', 'quantity': 0.5, 'unit': 'MWh'}),
            ('e4', {'period': '2026-11', 'department': '工艺试验室', 'activity': '电力', 'quantity': 900, 'unit': 'kWh'}),
            ('d1', {'period': '2026-11', 'department': '材料试验室', 'activity': '柴油', 'quantity': 100, 'unit': 'L'}),
        ]
        for key, data in drafts:
            db.add(Source(natural_key=key, data=data, revision=0))
        db.commit()
    with TestClient(app) as c:
        yield c


def login(client, role='admin'):
    token = client.post('/auth/login', json={'email': role + '@demo.local', 'password': 'Demo2026!'}).json()['token']
    return {'Authorization': 'Bearer ' + token}


def source_id(db, key):
    return db.query(Source).filter_by(natural_key=key).one().id


def make_bill(client, headers, name, lines):
    response = client.post('/bills', json={'name': name, 'lines': lines}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_bill_reconcile_and_claims(client):
    headers = login(client)
    bill = make_bill(client, headers, '材料十一月账单', [
        {'period': '2026-11', 'department': '材料试验室', 'quantity': 300, 'unit': 'kWh'},
    ])
    detail = client.post(f'/bills/{bill["id"]}/reconcile', headers=headers).json()
    assert detail['status'] == 'reconciled', detail
    line = detail['lines'][0]
    assert line['target_kwh'] == '300'
    assert sorted(claim['natural_key'] for claim in line['claims']) == ['e1', 'e2']
    assert sorted(claim['quantity_kwh'] for claim in line['claims']) == ['100', '200']


def test_second_bill_cannot_reclaim(client):
    headers = login(client)
    bill = make_bill(client, headers, '材料重复账单', [
        {'period': '2026-11', 'department': '材料试验室', 'quantity': 100, 'unit': 'kWh'},
    ])
    detail = client.post(f'/bills/{bill["id"]}/reconcile', headers=headers).json()
    # e1/e2 已认领，剩余 e3=500kWh 不能精确组成 100kWh
    assert detail['status'] == 'no_solution'
    assert any('认领' in row['report']['reason'] or '没有任何组合' in row['report']['reason'] for row in detail['lines'])
    assert all(not row["claims"] for row in detail['lines'])


def test_retry_is_idempotent(client):
    headers = login(client)
    bill = make_bill(client, headers, '幂等账单', [
        {'period': '2026-11', 'department': '工艺试验室', 'quantity': 0.9, 'unit': 'MWh'},
    ])
    first = client.post(f'/bills/{bill["id"]}/reconcile', headers=headers).json()
    assert first['status'] == 'reconciled'
    assert [claim['natural_key'] for claim in first['lines'][0]['claims']] == ['e4']
    second = client.post(f'/bills/{bill["id"]}/reconcile', headers=headers).json()
    assert second['status'] == 'reconciled'
    assert second['revision'] == first['revision']
    assert [claim['natural_key'] for claim in second['lines'][0]['claims']] == ['e4']


def test_source_edit_invalidates_claim(client):
    headers = login(client)
    bill = make_bill(client, headers, '失效账单', [
        {'period': '2026-11', 'department': '工艺试验室', 'quantity': 900, 'unit': 'kWh'},
    ])
    # 工艺 e4 已被幂等账单认领，所以这里应无解
    blocked = client.post(f'/bills/{bill["id"]}/reconcile', headers=headers).json()
    assert blocked['status'] == 'no_solution'

    # 修改材料室 e1：引用它的账单应整体失效并释放认领
    with Session() as db:
        e1 = source_id(db, 'e1')
    response = client.put(f'/sources/{e1}', headers=headers, json={'data': {
        'period': '2026-11', 'department': '材料试验室', 'activity': '电力', 'quantity': 120, 'unit': 'kWh'}})
    assert response.status_code == 200
    bills = client.get('/bills', headers=headers).json()
    material = next(row for row in bills if row['name'] == '材料十一月账单')
    assert material['status'] == 'stale'
    assert material['claim_count'] == 0
    detail = client.get(f'/bills/{material["id"]}', headers=headers).json()
    assert detail['status'] == 'stale'
    assert '失效' in detail['result']['explanation']


def test_bill_snapshot_feeds_run_without_mutation(client, monkeypatch):
    import src.services.report as report_service

    class FakeRedis:
        def rpush(self, *_args, **_kwargs):
            return 1

    monkeypatch.setattr(report_service.redis.Redis, 'from_url', classmethod(lambda cls, *a, **k: FakeRedis()))
    headers = login(client)
    bill = make_bill(client, headers, '可核算账单', [
        {'period': '2026-11', 'department': '材料试验室', 'quantity': 120, 'unit': 'kWh'},
    ])
    detail = client.post(f'/bills/{bill["id"]}/reconcile', headers=headers).json()
    assert detail['status'] == 'reconciled', detail
    policies = client.get('/policies', headers=headers).json()
    if not policies:
        pytest.skip('演示口径未登记')
    run = client.post('/runs', headers=headers, json={
        'name': '按账单核算', 'policy_id': policies[0]['id'], 'period': '全部', 'bill_id': bill['id'],
    }).json()
    assert run['snapshot']['bill']['id'] == bill['id']
    assert len(run['snapshot']['sources']) == 1
    assert run['snapshot']['sources'][0]['quantity'] == 120

    # 底稿随后被修改，报告快照保持不变
    with Session() as db:
        e1 = source_id(db, 'e1')
    client.put(f'/sources/{e1}', headers=headers, json={'data': {
        'period': '2026-11', 'department': '材料试验室', 'activity': '电力', 'quantity': 999, 'unit': 'kWh'}})
    stored = client.get(f'/runs/{run["id"]}', headers=headers).json()
    assert stored['snapshot']['sources'][0]['quantity'] == 120


def test_disputed_bill_cannot_feed_run(client):
    headers = login(client)
    bill = make_bill(client, headers, '争议账单', [
        {'period': '2026-11', 'department': '不存在部门', 'quantity': 10, 'unit': 'kWh'},
    ])
    disputed = client.post(f'/bills/{bill["id"]}/reconcile', headers=headers).json()
    assert disputed['status'] == 'no_solution'
    policies = client.get('/policies', headers=headers).json()
    if not policies:
        pytest.skip('演示口径未登记')
    response = client.post('/runs', headers=headers, json={
        'name': '争议核算', 'policy_id': policies[0]['id'], 'period': '全部', 'bill_id': bill['id'],
    })
    assert response.status_code == 409


def test_source_change_during_solve_invalidates_result(client, monkeypatch):
    headers = login(client)
    with Session() as db:
        for index, quantity in enumerate([1000, 2000]):
            db.add(Source(natural_key=f'r{index}', revision=0, data={
                'period': '2027-01', 'department': '竞态室', 'activity': '电力', 'quantity': quantity, 'unit': 'kWh'}))
        db.commit()
    bill = make_bill(client, headers, '竞态账单', [
        {'period': '2027-01', 'department': '竞态室', 'quantity': 3000, 'unit': 'kWh'},
    ])
    real_solve = bill_service.matching.solve

    def solve_with_concurrent_edit(lines, drafts, claimed_ids=()):
        result = real_solve(lines, drafts, claimed_ids)
        with Session() as db2:
            target = db2.query(Source).filter_by(natural_key='r0').one()
            target.data = {**target.data, 'quantity': 1500}
            target.revision = (target.revision or 0) + 1
            ledger.state_row(db2, lock=True).revision = 1
            db2.commit()
        return result

    monkeypatch.setattr(bill_service.matching, 'solve', solve_with_concurrent_edit)
    response = client.post(f'/bills/{bill["id"]}/reconcile', headers=headers)
    assert response.status_code == 409
    stored = client.get(f'/bills/{bill["id"]}', headers=headers).json()
    assert stored['status'] == 'draft'
    assert all(not row['claims'] for row in stored['lines'])


def test_concurrent_reconcile_claims_once(client):
    headers = login(client)
    with Session() as db:
        # 独立的一批底稿，避免与之前用例互相影响；三行各自唯一，整单唯一解
        for index, quantity in enumerate([100, 200, 400]):
            db.add(Source(natural_key=f'c{index}', revision=0, data={
                'period': '2026-12', 'department': '并发室', 'activity': '电力', 'quantity': quantity, 'unit': 'kWh'}))
        db.commit()
    lines = [
        {'period': '2026-12', 'department': '并发室', 'quantity': 100, 'unit': 'kWh'},
        {'period': '2026-12', 'department': '并发室', 'quantity': 200, 'unit': 'kWh'},
        {'period': '2026-12', 'department': '并发室', 'quantity': 400, 'unit': 'kWh'},
    ]
    bills = [make_bill(client, headers, f'并发账单{index}', lines) for index in range(2)]
    results = []

    def worker(bill_id):
        with Session() as db:
            from src.models.identity import User as U
            user = db.query(U).filter_by(email='admin@demo.local').one()
            try:
                obj = bill_service.reconcile(db, user, bill_id)
                results.append((bill_id, obj.status))
            except Exception as exc:  # 冲突时会话回滚
                db.rollback()
                results.append((bill_id, 'conflict:' + type(exc).__name__))

    threads = [threading.Thread(target=worker, args=(b['id'],)) for b in bills]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    statuses = sorted(status for _, status in results)
    assert statuses.count('reconciled') == 1, results
    with Session() as db:
        from src.models.bill import BillClaim
        total = sum(1 for claim in db.query(BillClaim).all() if (claim.data or {}).get('department') == '并发室')
    assert total == 3
