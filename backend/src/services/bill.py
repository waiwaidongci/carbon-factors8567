import time
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.models.source import Source
from src.models.bill import Bill, BillLine, BillClaim
from src.services import ledger, matching
from src.services.audit import log

BILL_UNITS = ('kWh', 'MWh')


def parse_target(quantity, unit):
    if unit not in BILL_UNITS:
        raise ValueError('账单电量单位只接受 kWh 或 MWh')
    value = matching.decimal_value(quantity)
    if value is None or value <= 0:
        raise ValueError('账单电量必须为正数')
    return matching.to_kwh(value, unit)


def normalize_line(raw, position):
    if not isinstance(raw, dict):
        raise ValueError('账单行格式不合法')
    period = raw.get('period')
    department = raw.get('department')
    for label, value in (('月份', period), ('部门', department)):
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
            raise ValueError(label + '不能为空或过长')
    unit = raw.get('unit', 'kWh')
    if unit not in BILL_UNITS:
        raise ValueError('账单电量单位只接受 kWh 或 MWh')
    quantity = matching.decimal_value(raw.get('quantity'))
    if quantity is None or quantity <= 0:
        raise ValueError('账单电量必须为正数')
    if quantity.as_tuple().exponent < -6:
        raise ValueError('账单电量最多 6 位小数')
    target = parse_target(quantity, unit)
    return {
        'position': position,
        'period': period.strip(),
        'department': department.strip(),
        'quantity': matching.dec_str(quantity),
        'unit': unit,
        'target_kwh': matching.dec_str(target),
    }


def normalize_lines(raw_lines):
    if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= 200:
        raise ValueError('账单至少 1 行且不超过 200 行')
    return [normalize_line(raw, index + 1) for index, raw in enumerate(raw_lines)]


def bill_lines(db, bill_id):
    rows = db.query(BillLine).filter(BillLine.bill_id == bill_id).order_by(BillLine.position).all()
    return [{
        'id': row.id,
        'position': row.position,
        'period': row.period,
        'department': row.department,
        'quantity': row.quantity,
        'unit': row.unit,
        'target_kwh': Decimal(row.target_kwh),
    } for row in rows]


def bill_claims(db, bill_id):
    rows = db.query(BillClaim).filter(BillClaim.bill_id == bill_id).order_by(BillClaim.line_id).all()
    return rows


def claim_brief(claim):
    return {
        'source_id': claim.source_id,
        'natural_key': claim.natural_key,
        'source_revision': claim.source_revision,
        'quantity': claim.quantity,
        'unit': claim.unit,
        'quantity_kwh': claim.quantity_kwh,
        'data': claim.data,
    }


def load_drafts(db):
    drafts = []
    for source in db.query(Source).order_by(Source.id).all():
        data = dict(source.data or {})
        kwh = matching.to_kwh(data.get('quantity'), data.get('unit'))
        if data.get('activity') != matching.ELECTRICITY_ACTIVITY or kwh is None:
            continue
        drafts.append({
            'id': source.id,
            'natural_key': source.natural_key,
            'revision': ledger.source_revision(source),
            'data': data,
            'kwh': kwh,
        })
    return drafts


def create(db, user, name, raw_lines):
    lines = normalize_lines(raw_lines)
    bill = Bill(name=name.strip(), status='draft', revision=0, created=time.time(), created_by=user.email)
    db.add(bill)
    db.flush()
    for line in lines:
        db.add(BillLine(bill_id=bill.id, **line))
    bill.revision = 1
    log(db, user.email, 'bill.create', bill.name)
    db.commit()
    return bill


def update(db, user, bill_id, name, raw_lines):
    # 与对账相同的锁顺序：先账本、后账单。
    ledger.state_row(db, lock=True, fresh=True)
    bill = db.query(Bill).filter(Bill.id == bill_id).with_for_update().populate_existing().first()
    if not bill:
        raise HTTPException(404, '账单不存在')
    if bill.status == 'reconciled':
        raise HTTPException(409, '已对平的账单不可改行；请另建账单，原认领保持有效')
    lines = normalize_lines(raw_lines)
    ledger.release_bill_claims(db, bill.id)
    db.query(BillLine).filter(BillLine.bill_id == bill.id).delete(synchronize_session=False)
    bill.name = name.strip()
    bill.status = 'draft'
    bill.result = None
    bill.ledger_revision = None
    bill.revision = (bill.revision or 0) + 1
    for line in lines:
        db.add(BillLine(bill_id=bill.id, **line))
    log(db, user.email, 'bill.update', bill.name)
    db.commit()
    return bill


def reconcile(db, user, bill_id):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, '账单不存在')
    if bill.status == 'reconciled':
        return bill
    lines = bill_lines(db, bill.id)
    if not lines:
        raise HTTPException(422, '账单没有可对账的行')
    lines_revision = bill.revision or 0
    start_revision = ledger.current_revision(db)

    claimed_ids = {row[0] for row in db.query(BillClaim.source_id).filter(BillClaim.bill_id != bill.id).all()}
    drafts = load_drafts(db)
    # 求解在锁外完成，避免长时间持有写锁；生效前重新校验账本与账单版本。
    result = matching.solve(lines, drafts, claimed_ids)
    result['ledger_revision'] = start_revision
    result['solved_at'] = time.time()

    matched_ids = []
    if result['status'] == 'unique':
        for briefs in result['matches'].values():
            matched_ids.extend(item['source_id'] for item in briefs)
    locked, already = _lock_and_verify(db, bill.id, lines_revision, start_revision, drafts, matched_ids)
    if already:
        # 并发下另一请求已整单生效：本次重试不新增认领，直接返回既有结果。
        db.commit()
        return locked

    ledger.release_bill_claims(db, bill.id)
    db.flush()
    locked.solved = time.time()
    locked.result = result
    if result['status'] != 'unique':
        locked.status = result['status']
        locked.ledger_revision = None
        log(db, user.email, 'bill.dispute', f'{locked.name} {result["status"]}')
        db.commit()
        return locked

    by_id = {draft['id']: draft for draft in drafts}
    for line in lines:
        for brief in result['matches'][line['id']]:
            draft = by_id[brief['source_id']]
            db.add(BillClaim(
                bill_id=locked.id,
                line_id=line['id'],
                source_id=draft['id'],
                natural_key=draft.get('natural_key'),
                source_revision=draft['revision'],
                quantity=str(draft['data'].get('quantity')),
                unit=str(draft['data'].get('unit')),
                quantity_kwh=matching.dec_str(draft['kwh']),
                data=draft['data'],
                created=time.time(),
            ))
    locked.status = 'reconciled'
    locked.ledger_revision = start_revision
    locked.revision = (locked.revision or 0) + 1
    log(db, user.email, 'bill.reconcile', locked.name)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '底稿已被其他账单认领，本次对账未生效；请重新对账')
    return locked


def _lock_and_verify(db, bill_id, lines_revision, start_revision, drafts, matched_ids):
    # 锁顺序固定为「账本 → 账单」，与底稿写入路径一致，避免死锁。
    state = ledger.state_row(db, lock=True, fresh=True)
    locked = db.query(Bill).filter(Bill.id == bill_id).with_for_update().populate_existing().first()
    if not locked:
        db.rollback()
        raise HTTPException(404, '账单不存在')
    if locked.status == 'reconciled':
        return locked, True
    if (state.revision or 0) != start_revision:
        db.rollback()
        raise HTTPException(409, '求解期间底稿发生变更，本次对账作废；底稿未认领，请重新对账')
    if (locked.revision or 0) != lines_revision:
        db.rollback()
        raise HTTPException(409, '求解期间账单被修改，本次对账作废；底稿未认领，请重新对账')
    by_id = {draft['id']: draft for draft in drafts}
    fresh = db.query(Source).filter(Source.id.in_(matched_ids)).populate_existing().all() if matched_ids else []
    for source in fresh:
        expected = by_id[source.id]['revision']
        if ledger.source_revision(source) != expected:
            db.rollback()
            raise HTTPException(409, '求解期间底稿发生变更，本次对账作废；底稿未认领，请重新对账')
    if matched_ids:
        taken = db.query(BillClaim).filter(
            BillClaim.source_id.in_(matched_ids), BillClaim.bill_id != bill_id).first()
        if taken:
            db.rollback()
            raise HTTPException(409, '底稿已被其他账单认领，本次对账未生效；请重新对账')
    return locked, False
    return locked


def detail(db, bill):
    lines = bill_lines(db, bill.id)
    claims = bill_claims(db, bill.id)
    claims_by_line = {}
    for claim in claims:
        claims_by_line.setdefault(claim.line_id, []).append(claim_brief(claim))
    result = bill.result or {}
    reports = {row.get('line_id'): row for row in result.get('line_reports', [])}
    line_payload = []
    for line in lines:
        report = dict(reports.get(line['id'], {}))
        report.pop('line_id', None)
        line_payload.append({
            **line,
            'target_kwh': matching.dec_str(line['target_kwh']),
            'claims': sorted(claims_by_line.get(line['id'], []), key=lambda item: item['source_id']),
            'report': report,
        })
    return {
        'id': bill.id,
        'name': bill.name,
        'status': bill.status,
        'revision': bill.revision,
        'ledger_revision': bill.ledger_revision,
        'created': bill.created,
        'solved': bill.solved,
        'created_by': bill.created_by,
        'lines': line_payload,
        'result': {
            'status': result.get('status'),
            'explanation': result.get('explanation'),
            'truncated': result.get('truncated', False),
            'solutions': result.get('solutions', []),
            'ledger_revision': result.get('ledger_revision'),
            'solved_at': result.get('solved_at'),
        },
    }


def summary(db, bill):
    lines = db.query(BillLine).filter(BillLine.bill_id == bill.id).count()
    claims = db.query(BillClaim).filter(BillClaim.bill_id == bill.id).count()
    result = bill.result or {}
    return {
        'id': bill.id,
        'name': bill.name,
        'status': bill.status,
        'revision': bill.revision,
        'ledger_revision': bill.ledger_revision,
        'line_count': lines,
        'claim_count': claims,
        'created': bill.created,
        'solved': bill.solved,
        'created_by': bill.created_by,
        'explanation': result.get('explanation'),
    }


def reconcile_available(db):
    return [{'id': bill.id, 'name': bill.name, 'revision': bill.revision, 'status': bill.status}
            for bill in db.query(Bill).filter(Bill.status == 'reconciled').order_by(Bill.id).all()]
