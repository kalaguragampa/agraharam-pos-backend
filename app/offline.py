"""Signed catalogue snapshots allow offline bills to retain their charged prices."""
import base64, hashlib, hmac, json, os, secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import config
from .db import get_db
from .models import User, MenuItem, CompanySettings, Customer, Order, Audit, Branch
from .schemas import OfflineOrderIn, OrderIn
from .security import require

router=APIRouter(prefix='/offline')

def signing_key():
    configured=config.offline_signing_key or os.environ.get('OFFLINE_SIGNING_KEY')
    if configured:return configured.encode()
    if config.app_env=='production':raise HTTPException(503,'Configure OFFLINE_SIGNING_KEY before enabling offline billing')
    path=Path(__file__).parent.parent/'.offline_signing_key'
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_hex(32))
    except FileExistsError:pass
    return path.read_text().strip().encode()

def encode(payload):
    data=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode()
    return data+'.'+hmac.new(signing_key(),data.encode(),hashlib.sha256).hexdigest()

def decode(token):
    try:
        data,signature=token.rsplit('.',1)
        expected=hmac.new(signing_key(),data.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,signature):raise ValueError()
        return json.loads(base64.urlsafe_b64decode(data))
    except (ValueError,TypeError,UnicodeError):raise HTTPException(422,'Offline catalogue signature is invalid')

@router.get('/catalogue')
def catalogue(user: User=Depends(require('billing')),db: Session=Depends(get_db)):
    settings=db.get(CompanySettings,1)
    if not settings:raise HTTPException(503,'Initialize database first')
    now=datetime.now(timezone.utc)
    items=[{'item_id':i.id,'name':i.name,'category':i.category,'price':str(i.price),**{k:v for k,v in (i.service_details or {}).items() if k!='image'}} for i in db.scalars(select(MenuItem).where(MenuItem.active.is_(True)))]
    payload={'user_id':user.id,'staff_name':user.name,'staff_username':user.username or '','issued_at':now.isoformat(),'expires_at':(now+timedelta(days=3)).isoformat(),'items':items,'tax_rate':str(settings.tax_rate),'address':settings.address,'phone':settings.phone,'seller_gstin':settings.gstin,'seller_state':settings.state_code,'receipt_details':{'business_name':settings.business_name,'branch_name':settings.branch_name,'legal_name':settings.legal_name,'fssai_number':settings.fssai_number,'gstin':settings.gstin,'address':settings.address,'phone':settings.phone}}
    return {**payload,'catalogue_token':encode(payload),'images':{i.id:(i.service_details or {}).get('image','') for i in db.scalars(select(MenuItem).where(MenuItem.active.is_(True)))}}

@router.post('/orders',status_code=201)
def sync_order(body: OfflineOrderIn,user: User=Depends(require('billing')),db: Session=Depends(get_db)):
    from .main import order_view, invoice_number, tax_lines
    order=body.order;request_body=order.model_dump(mode='json')
    existing=db.scalar(select(Order).where(Order.request_id==str(order.request_id)))
    if existing:
        if existing.staff_id!=user.id or OrderIn.model_validate(existing.request_body).model_dump(mode='json')!=request_body:raise HTTPException(409,'Request identifier belongs to another bill')
        return order_view(existing)
    snapshot=decode(body.catalogue_token)
    if snapshot.get('user_id')!=user.id:raise HTTPException(403,'Sign in as the staff member who created this offline bill')
    if body.created_at.tzinfo is None:raise HTTPException(422,'Offline bill time must include a timezone')
    created=body.created_at.astimezone(timezone.utc);now=datetime.now(timezone.utc)
    issued=datetime.fromisoformat(snapshot['issued_at']);expires=datetime.fromisoformat(snapshot['expires_at'])
    # The device records the sale immediately before fetching its signed catalogue.\n    # Allow only a small clock/round-trip skew; keep all other signature and total checks.\n    if created<issued-timedelta(seconds=5) or created>expires or created>now+timedelta(minutes=5):raise HTTPException(409,'Offline bill time is outside the cached catalogue validity period')
    if now>expires+timedelta(days=7):raise HTTPException(409,'Offline bill is too old for automatic sync; administrator review required')
    prices={i['item_id']:i for i in snapshot['items']};quantities={}
    for line in order.items:
        key=str(line.item_id);quantities[key]=quantities.get(key,0)+line.qty
        if quantities[key]>1000:raise HTTPException(422,'Maximum item quantity is 1000')
    lines=[];total=Decimal('0')
    for key,qty in quantities.items():
        if key not in prices:raise HTTPException(409,'Item is missing from the signed offline catalogue')
        item=prices[key];amount=Decimal(item['price'])*qty;total+=amount
        lines.append({**item,'qty':qty,'line_total':str(amount)})
    if total!=order.expected_total:raise HTTPException(409,'Offline total does not match the signed item prices')
    customer=db.get(Customer,str(order.customer_id)) if order.customer_id else None
    if order.customer_id and not customer:raise HTTPException(409,'Offline customer needs administrator review')
    rate=Decimal(snapshot['tax_rate']);subtotal,calculated_tax,cgst,sgst,igst=tax_lines(lines,rate,snapshot.get('seller_state',''),order.place_of_supply)
    from .gst import tax_snapshot
    from .payments import payment_details
    gst=tax_snapshot(snapshot.get('seller_gstin',''),snapshot.get('seller_state',''),order.buyer_gstin,order.place_of_supply,calculated_tax,total)
    if gst.get('classification')!='UNCLASSIFIED':gst.update(cgst=str(cgst),sgst=str(sgst),igst=str(igst))
    branch_id=order.branch_id or user.branch_id
    if not branch_id or (not user.is_admin and branch_id!=user.branch_id):raise HTTPException(403,'Offline bill branch does not match cashier assignment')
    branch=db.get(Branch,branch_id)
    if not branch or not branch.active:raise HTTPException(409,'Offline bill branch needs administrator review')
    saved=Order(branch_id=branch_id,gst_details=gst,receipt_details=snapshot.get('receipt_details',{}),request_id=str(order.request_id),request_body=request_body,staff_id=user.id,staff_name=snapshot.get('staff_name',user.name),staff_username=snapshot.get('staff_username',user.username or ''),customer_id=customer.id if customer else None,customer_name=customer.name if customer else '',order_type=order.order_type,payment=order.payment,payment_details=payment_details(order),items=lines,total=total,subtotal=subtotal,tax=total-subtotal,tax_rate=rate,address=snapshot['address'],phone=snapshot['phone'],created_at=created)
    db.add(saved)
    from sqlalchemy.exc import IntegrityError
    try:
        saved.bill_number=invoice_number(db,branch,created)
        db.flush();db.add(Audit(actor_id=user.id,action='bill.offline_synced',target_id=str(saved.id),details={'request_id':saved.request_id,'total':str(total),'payment':saved.payment}));db.commit()
    except IntegrityError:
        db.rollback();existing=db.scalar(select(Order).where(Order.request_id==str(order.request_id)))
        if existing and existing.staff_id==user.id and OrderIn.model_validate(existing.request_body).model_dump(mode='json')==request_body:return order_view(existing)
        raise HTTPException(409,'Offline bill could not be synchronized; keep it for review')
    return order_view(saved)
