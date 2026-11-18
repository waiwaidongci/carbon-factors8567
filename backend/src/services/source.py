from fastapi import HTTPException
from src.models.source import Source, Policy, Period
from src.services.validation import validate_source, validate_policy
from src.services.audit import log
from src.services import ledger
import uuid


def save_source(db, user, data, id=None):
    validate_source(data)
    # 与对账一致：先锁账本，再读改底稿，避免锁顺序相反造成死锁。
    state = ledger.state_row(db, lock=True)
    obj = db.get(Source, id) if id else Source(natural_key='user-' + uuid.uuid4().hex, revision=0)
    if not obj:
        db.rollback()
        raise HTTPException(404, '底稿不存在')
    changed = id is not None and (obj.data or {}) != data
    if id is None or changed:
        state.revision = (state.revision or 0) + 1
    obj.data = data
    if changed:
        obj.revision = (obj.revision or 0) + 1
        ledger.invalidate_for_sources(db, [obj.id], '底稿在账单对平后被修改，原认领失效；请重新对账。')
    db.add(obj)
    if not db.query(Period).filter_by(name=data['period']).first():
        db.add(Period(name=data['period'], description='业务来源自动登记期间'))
    log(db, user.email, 'source.update' if id else 'source.create', obj.natural_key)
    db.commit()
    return obj


def delete_source(db, user, id):
    state = ledger.state_row(db, lock=True)
    obj = db.get(Source, id)
    if not obj:
        db.rollback()
        raise HTTPException(404, '底稿不存在')
    state.revision = (state.revision or 0) + 1
    ledger.invalidate_for_sources(db, [obj.id], '底稿在账单对平后被删除，原认领失效；请重新对账。')
    db.delete(obj)
    log(db, user.email, 'source.delete', id)
    db.commit()
    return {'deleted': id}


def save_policy(db, user, data):
    validate_policy(data)
    if db.query(Policy).filter_by(version=data['version']).first():
        raise HTTPException(409, '版本名已存在，请另建版本保留历史')
    obj = Policy(version=data['version'], data=data)
    db.add(obj)
    log(db, user.email, 'policy.version', data['version'])
    db.commit()
    return obj
