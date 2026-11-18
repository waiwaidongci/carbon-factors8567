from sqlalchemy import Column, Integer, String, JSON, Float, ForeignKey
from src.config.database import Base


class LedgerState(Base):
    __tablename__ = 'ledger_state'
    id = Column(Integer, primary_key=True)
    revision = Column(Integer, nullable=False, default=0)


class Bill(Base):
    __tablename__ = 'electricity_bills'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    status = Column(String(20), nullable=False, default='draft')
    revision = Column(Integer, nullable=False, default=0)
    ledger_revision = Column(Integer)
    result = Column(JSON)
    created = Column(Float)
    solved = Column(Float)
    created_by = Column(String(120))


class BillLine(Base):
    __tablename__ = 'bill_lines'
    id = Column(Integer, primary_key=True)
    bill_id = Column(Integer, ForeignKey('electricity_bills.id'), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    period = Column(String(100), nullable=False)
    department = Column(String(100), nullable=False)
    quantity = Column(String(64), nullable=False)
    unit = Column(String(16), nullable=False)
    target_kwh = Column(String(64), nullable=False)


class BillClaim(Base):
    __tablename__ = 'bill_claims'
    id = Column(Integer, primary_key=True)
    bill_id = Column(Integer, ForeignKey('electricity_bills.id'), nullable=False, index=True)
    line_id = Column(Integer, ForeignKey('bill_lines.id'), nullable=False, index=True)
    source_id = Column(Integer, nullable=False, unique=True)
    natural_key = Column(String(120))
    source_revision = Column(Integer, nullable=False, default=0)
    quantity = Column(String(64), nullable=False)
    unit = Column(String(16), nullable=False)
    quantity_kwh = Column(String(64), nullable=False)
    data = Column(JSON, nullable=False)
    created = Column(Float)
