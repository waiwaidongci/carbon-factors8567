from fastapi import APIRouter, Depends, HTTPException
from src.config.database import database
from src.controllers.auth import current, editor
from src.models.bill import Bill
from src.schemas.requests import BillInput
from src.services import bill as bill_service
from src.utils.serialize import row

router = APIRouter()


@router.get('/bills')
def bills(db=Depends(database), u=Depends(current)):
    return [bill_service.summary(db, obj) for obj in db.query(Bill).order_by(Bill.id.desc()).all()]


@router.get('/bills/reconciled')
def reconciled(db=Depends(database), u=Depends(current)):
    return bill_service.reconcile_available(db)


@router.get('/bills/{id}')
def detail(id: int, db=Depends(database), u=Depends(current)):
    obj = db.get(Bill, id)
    if not obj:
        raise HTTPException(404, '账单不存在')
    return bill_service.detail(db, obj)


@router.post('/bills')
def create(body: BillInput, db=Depends(database), u=Depends(editor)):
    obj = bill_service.create(db, u, body.name, [line.model_dump() for line in body.lines])
    return bill_service.detail(db, obj)


@router.put('/bills/{id}')
def update(id: int, body: BillInput, db=Depends(database), u=Depends(editor)):
    obj = bill_service.update(db, u, id, body.name, [line.model_dump() for line in body.lines])
    return bill_service.detail(db, obj)

@router.post('/bills/{id}/reconcile')
def reconcile(id: int, db=Depends(database), u=Depends(editor)):
    obj = bill_service.reconcile(db, u, id)
    return bill_service.detail(db, obj)
