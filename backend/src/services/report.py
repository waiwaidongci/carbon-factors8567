import hashlib, json, time
from fastapi import HTTPException
from src.models.source import Source, Policy
from src.models.bill import Bill, BillClaim
from src.models.run import Run
from src.services.audit import log
from src.config.settings import REDIS_URL
import redis


def _bill_snapshot(db, bill_id):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(404, '账单不存在')
    if bill.status != 'reconciled':
        raise HTTPException(409, '仅已对平且认领有效的账单可用于核算；有争议或已失效的账单不可用')
    claims = db.query(BillClaim).filter(BillClaim.bill_id == bill.id).order_by(BillClaim.source_id).all()
    if not claims:
        raise HTTPException(409, '账单没有有效认领底稿，不能用于核算')
    sources = [dict(claim.data) for claim in claims]
    lines = []
    for claim in claims:
        lines.append({
            'line_id': claim.line_id,
            'source_id': claim.source_id,
            'source_revision': claim.source_revision,
            'quantity': claim.quantity,
            'unit': claim.unit,
            'quantity_kwh': claim.quantity_kwh,
        })
    return sources, {
        'id': bill.id,
        'name': bill.name,
        'revision': bill.revision,
        'ledger_revision': bill.ledger_revision,
        'claims': lines,
    }


def submit(db, user, body):
    policy = db.get(Policy, body.policy_id)
    if not policy:
        raise HTTPException(404, '口径版本不存在')
    bill_meta = None
    if body.bill_id is not None:
        sources, bill_meta = _bill_snapshot(db, body.bill_id)
        period = '账单认领：' + bill_meta['name']
    else:
        sources = [x.data for x in db.query(Source).order_by(Source.id).all()
                   if body.period == '全部' or x.data.get('period') == body.period]
        period = body.period
    if not sources:
        raise HTTPException(422, '所选期间没有底稿')
    snapshot = {'policy': policy.data, 'sources': sources, 'period': period}
    if bill_meta:
        snapshot['bill'] = bill_meta
    checksum = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    obj = Run(name=body.name, status='queued', snapshot=snapshot, checksum=checksum, created=time.time(), approved=False)
    db.add(obj)
    log(db, user.email, 'run.queue', body.name)
    db.commit()
    redis.Redis.from_url(REDIS_URL).rpush('report-jobs', obj.id)
    return obj


def approve(db, user, id, note):
    obj = db.get(Run, id)
    if not obj:
        raise HTTPException(404, '报告不存在')
    if obj.status != 'completed':
        raise HTTPException(409, '仅可复核已完成报告')
    if obj.approved:
        raise HTTPException(409, '报告已经复核')
    obj.approved = True
    obj.review = note
    log(db, user.email, 'run.approve', id)
    db.commit()
    return obj
