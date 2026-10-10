"""Outlet-wide cash reconciliation with immutable closing snapshots."""
import hashlib,json
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from .db import get_db
from .models import CashDay,CashMovement,Order,Audit,User
from .schemas import Payload
from .security import require,admin
from .closing_lock import lock_day
IST=ZoneInfo('Asia/Kolkata');router=APIRouter(prefix='/closing');ZERO=Decimal('0')
Money=Decimal
class OpenIn(Payload):
    opening_cash: Money=Field(ge=0,le=1000000000,max_digits=14,decimal_places=2)
class MovementIn(Payload):
    request_id: UUID
    kind: Literal['Cash in','Cash out','Expense','Refund']
    amount: Money=Field(gt=0,le=1000000000,max_digits=14,decimal_places=2)
    reason: str=Field(min_length=3,max_length=300)
    bill_id: int|None=Field(default=None,gt=0)
class CloseIn(Payload):
    counted_cash: Money=Field(ge=0,le=1000000000,max_digits=14,decimal_places=2)
    fingerprint: str=Field(min_length=64,max_length=64)
    all_counters_synced: Literal[True]
    notes: str=Field(default='',max_length=500)
class ReasonIn(Payload):
    reason: str=Field(min_length=3,max_length=300)
def event(db,u,action,target,details=None):db.add(Audit(actor_id=u.id,action=action,target_id=str(target),details=details or {}))
def valid_day(day):
    if day>datetime.now(IST).date():raise HTTPException(422,'Future business dates are not allowed')
def get_day(db,day):
    d=db.get(CashDay,day.isoformat())
    if not d:raise HTTPException(409,'Set opening cash for this day first')
    return d
def ensure_open(d):
    if d.status!='Open':raise HTTPException(409,'This day is closed. An administrator must reopen it before cash adjustments.')
def movement_view(m):return {'id':m.id,'kind':m.kind,'amount':m.amount,'reason':m.reason,'bill_id':m.bill_id,'actor_name':m.actor_name,'active':m.active,'void_reason':m.void_reason,'created_at':m.created_at}
def summary(db,day):
    start=datetime.combine(day,time.min,tzinfo=IST).astimezone(timezone.utc);end=start+timedelta(days=1)
    orders=list(db.scalars(select(Order).where(Order.created_at>=start,Order.created_at<end).order_by(Order.id)))
    movements=list(db.scalars(select(CashMovement).where(CashMovement.day==day.isoformat()).order_by(CashMovement.id)))
    d=db.get(CashDay,day.isoformat());collections={p:ZERO for p in ['Cash','UPI','Card']};cancelled={p:ZERO for p in collections}
    staff={}
    for o in orders:
        collections[o.payment]+=o.total
        if o.status=='Cancelled':cancelled[o.payment]+=o.total
        # Snapshot names remain unchanged when user profiles are later edited.
        key=(o.staff_id,o.staff_name,o.staff_username)
        row=staff.setdefault(key,{'user_id':o.staff_id,'name':o.staff_name,'username':o.staff_username,'bills':0,'Cash':ZERO,'UPI':ZERO,'Card':ZERO,'cancelled':ZERO})
        row['bills']+=1;row[o.payment]+=o.total
        if o.status=='Cancelled':row['cancelled']+=o.total
    amounts={k:sum((m.amount for m in movements if m.active and m.kind==k),ZERO) for k in ['Cash in','Cash out','Expense','Refund']}
    opening=d.opening_cash if d else ZERO
    expected=opening+collections['Cash']+amounts['Cash in']-amounts['Cash out']-amounts['Expense']-amounts['Refund']
    fingerprint=hashlib.sha256(json.dumps({'day':day.isoformat(),'opening':str(opening),'orders':[(o.id,o.status,str(o.total),o.payment) for o in orders],'moves':[(m.id,m.active,m.kind,str(m.amount),m.bill_id) for m in movements]},sort_keys=True).encode()).hexdigest()
    closing=d.closing if d else {}
    return {'day':day,'status':d.status if d else 'Not opened','opening_cash':opening,'collections':collections,'cancelled_collections':cancelled,'completed_sales':sum(collections.values())-sum(cancelled.values()),'bill_count':len(orders),'movements':list(map(movement_view,movements)),'adjustments':amounts,'expected_cash':expected,'fingerprint':fingerprint,'staff':list(staff.values()),'closing':closing,'history':d.history if d else [],'changed_after_close':bool(closing and closing.get('fingerprint')!=fingerprint)}
@router.get('/{day}')
def read(day:date,user:User=Depends(require('closing')),db:Session=Depends(get_db)):
    valid_day(day);return summary(db,day)
@router.post('/{day}/open')
def open_day(day:date,body:OpenIn,user:User=Depends(require('closing')),db:Session=Depends(get_db)):
    valid_day(day);lock_day(db,day)
    existing=db.get(CashDay,day.isoformat())
    if existing:
        if existing.opening_cash==body.opening_cash:return summary(db,day)
        raise HTTPException(409,'Opening cash is already recorded; use a documented cash adjustment')
    db.add(CashDay(day=day.isoformat(),opening_cash=body.opening_cash,opened_by=user.id,opened_name=user.name));event(db,user,'cash.opened',day,{'opening_cash':str(body.opening_cash)});db.commit();return summary(db,day)
@router.post('/{day}/movements',status_code=201)
def move(day:date,body:MovementIn,user:User=Depends(require('closing')),db:Session=Depends(get_db)):
    valid_day(day);lock_day(db,day)
    old=db.scalar(select(CashMovement).where(CashMovement.request_id==str(body.request_id)))
    if old:
        if (old.day,old.actor_id,old.kind,old.amount,old.reason,old.bill_id)!=(day.isoformat(),user.id,body.kind,body.amount,body.reason,body.bill_id):raise HTTPException(409,'Adjustment reference already used')
        return summary(db,day)
    d=get_day(db,day);ensure_open(d)
    if body.kind=='Refund':
        if not body.bill_id:raise HTTPException(422,'Cash refunds must reference a cancelled bill number')
        order=db.scalar(select(Order).where(Order.id==body.bill_id).with_for_update())
        if not order or order.status!='Cancelled':raise HTTPException(422,'Refund requires a cancelled bill')
        refunded=db.scalar(select(func.coalesce(func.sum(CashMovement.amount),0)).where(CashMovement.bill_id==body.bill_id,CashMovement.kind=='Refund',CashMovement.active.is_(True)))
        if refunded+body.amount>order.total:raise HTTPException(409,'Cash refunds cannot exceed the cancelled bill total')
    elif body.bill_id:raise HTTPException(422,'Bill reference is only used for refunds')
    m=CashMovement(day=day.isoformat(),request_id=str(body.request_id),kind=body.kind,amount=body.amount,reason=body.reason,bill_id=body.bill_id,actor_id=user.id,actor_name=user.name);db.add(m);db.flush();event(db,user,'cash.adjusted',m.id,{'kind':m.kind,'amount':str(m.amount),'reason':m.reason,'bill_id':m.bill_id});db.commit();return summary(db,day)
@router.post('/{day}/close')
def close_day(day:date,body:CloseIn,user:User=Depends(require('closing')),db:Session=Depends(get_db)):
    valid_day(day);lock_day(db,day);d=get_day(db,day)
    if d.status=='Closed':
        if d.closing.get('fingerprint')==body.fingerprint and Decimal(d.closing['counted_cash'])==body.counted_cash:return summary(db,day)
        raise HTTPException(409,'This day is already closed')
    report=summary(db,day)
    if report['fingerprint']!=body.fingerprint:raise HTTPException(409,'Bills or cash movements changed. Refresh and review the latest totals.')
    difference=body.counted_cash-report['expected_cash']
    if difference and len(body.notes)<3:raise HTTPException(422,'Explain the cash shortage or excess in closing notes')
    serial=json.loads(json.dumps({k:report[k] for k in ['collections','cancelled_collections','adjustments','staff','bill_count']},default=str))
    d.closing={**serial,'fingerprint':body.fingerprint,'expected_cash':str(report['expected_cash']),'counted_cash':str(body.counted_cash),'difference':str(difference),'notes':body.notes,'closed_by':user.id,'closed_name':user.name,'closed_username':user.username,'closed_at':datetime.now(timezone.utc).isoformat(),'all_counters_synced':True};d.status='Closed';event(db,user,'cash.closed',day,d.closing);db.commit();return summary(db,day)
@router.post('/{day}/reopen')
def reopen(day:date,body:ReasonIn,user:User=Depends(admin),db:Session=Depends(get_db)):
    valid_day(day);lock_day(db,day);d=get_day(db,day)
    if d.status!='Closed':raise HTTPException(409,'This day is already open')
    d.history=[*d.history,{'closing':d.closing,'reopened_by':user.name,'reason':body.reason,'at':datetime.now(timezone.utc).isoformat()}];d.status='Open';d.closing={};event(db,user,'cash.reopened',day,{'reason':body.reason});db.commit();return summary(db,day)
@router.post('/{day}/movements/{id}/void')
def void(day:date,id:int,body:ReasonIn,user:User=Depends(admin),db:Session=Depends(get_db)):
    valid_day(day);lock_day(db,day);d=get_day(db,day);ensure_open(d);m=db.get(CashMovement,id)
    if not m or m.day!=day.isoformat():raise HTTPException(404,'Cash adjustment not found')
    if m.active:m.active=False;m.void_reason=body.reason;event(db,user,'cash.adjustment_voided',id,{'reason':body.reason});db.commit()
    return summary(db,day)
