from sqlalchemy.exc import IntegrityError
from src.models.bill import LedgerState, Bill, BillClaim


def _query(db, lock=False, fresh=False):
    query = db.query(LedgerState).filter(LedgerState.id == 1)
    if fresh:
        query = query.populate_existing()
    if lock:
        query = query.with_for_update()
    return query


def state_row(db, lock=False, fresh=False):
    state = _query(db, lock, fresh).first()
    if state:
        return state
    try:
        with db.begin_nested():
            db.add(LedgerState(id=1, revision=0))
    except IntegrityError:
        pass
    return _query(db, lock, fresh).one()


def current_revision(db):
    state = _query(db, fresh=True).first()
    return state.revision if state else 0


def source_revision(source):
    return getattr(source, 'revision', 0) or 0


def invalidate_for_sources(db, source_ids, reason):
    """底稿被改写或删除时，凡引用这些底稿的已对平账单整体作废并释放认领。"""
    ids = [int(value) for value in source_ids]
    if not ids:
        return []
    bill_ids = [row[0] for row in db.query(BillClaim.bill_id).filter(BillClaim.source_id.in_(ids)).distinct().all()]
    for bill_id in bill_ids:
        bill = db.get(Bill, bill_id)
        db.query(BillClaim).filter(BillClaim.bill_id == bill_id).delete(synchronize_session=False)
        if bill:
            result = dict(bill.result or {})
            result['status'] = 'stale'
            result['explanation'] = reason
            bill.result = result
            bill.status = 'stale'
            bill.ledger_revision = None
    db.flush()
    return bill_ids


def release_bill_claims(db, bill_id):
    db.query(BillClaim).filter(BillClaim.bill_id == bill_id).delete(synchronize_session=False)
    db.flush()
