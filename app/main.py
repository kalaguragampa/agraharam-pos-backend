import csv, io, secrets
from datetime import datetime, timedelta, timezone, date, time
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete, func, text, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import config
from .db import get_db
from .models import User, LoginSession, LoginAttempt, MenuItem, Customer, CompanySettings, Order, Audit, Category, Branch
from .schemas import LoginIn, UserIn, UserEdit, MenuIn, CustomerIn, SettingsIn, OrderIn, PERMISSIONS, CategoryIn, CancelIn
from .security import current_user, require, admin, hasher, DUMMY_HASH, token_hash, user_view

from .passcodes import encrypt, decrypt
from .gst import tax_snapshot, gst_report_data
from .payments import payment_details, allocations, line_allocations

app=FastAPI(title='Agraharam Item POS API',version='1.0.0')
app.add_middleware(CORSMiddleware,allow_origins=config.origins,allow_credentials=True,allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type'])

@app.middleware('http')
async def protect_origin(request: Request, call_next):
    if request.scope['path'].startswith('/api/'):
        request.scope['path']=request.scope['path'][4:]
        request.scope['raw_path']=request.scope['path'].encode()
    # Cookie authentication requires an allowed browser Origin on every mutation,
    # including login, to prevent CSRF and login-CSRF. Non-browser clients must set it too.
    if request.method in ('POST','PUT','PATCH','DELETE') and request.headers.get('origin','').rstrip('/') not in config.origins:
        return JSONResponse({'detail':'Request origin is not allowed'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    return response

@app.get('/health')
def health(db: Session=Depends(get_db)):
    db.execute(text('SELECT 1'));return {'status':'ok','database':'connected'}

def audit(db,user,action,target,details=None):
    db.add(Audit(actor_id=user.id,action=action,target_id=str(target),details=details or {}))
def get_or_404(db,model,id):
    item=db.get(model,str(id))
    if not item: raise HTTPException(404,'Record not found')
    return item

def settings(db):
    s=db.get(CompanySettings,1)
    if not s: raise HTTPException(503,'Database has not been initialized; run python -m app.seed')
    return s

@app.post('/auth/login')
def login(body: LoginIn, response: Response, db: Session=Depends(get_db)):
    email=str(body.username or body.email).strip().lower();now=datetime.now(timezone.utc);cutoff=now-timedelta(minutes=15)
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at<cutoff))
    db.execute(delete(LoginSession).where(LoginSession.expires_at<now))
    account=db.scalar(select(User).where(or_(User.email==email,User.username==email)))
    email=account.email if account else email
    failures=db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.email==email,LoginAttempt.created_at>=cutoff))
    if failures>=5:
        db.commit();raise HTTPException(429,'Too many attempts. Try again after 15 minutes.')
    user=account
    valid=hasher.verify(body.passcode or body.password,user.password_hash if user else DUMMY_HASH)
    if not user or not valid or not user.active:
        db.add(LoginAttempt(email=email));db.commit();raise HTTPException(401,'Invalid username or passcode')
    db.execute(delete(LoginAttempt).where(LoginAttempt.email==email))
    token=secrets.token_urlsafe(48)
    db.add(LoginSession(token_hash=token_hash(token),user_id=user.id,expires_at=now+timedelta(hours=config.session_hours)))
    db.commit()
    response.set_cookie('agraharam_session',token,max_age=config.session_hours*3600,httponly=True,secure=config.cookie_secure,samesite='lax',path='/')
    return user_view(user)

@app.get('/auth/me')
def me(user: User=Depends(current_user)):return user_view(user)
@app.post('/auth/logout')
def logout(request: Request,response: Response,db: Session=Depends(get_db)):
    token=request.cookies.get('agraharam_session')
    if token: db.execute(delete(LoginSession).where(LoginSession.token_hash==token_hash(token)));db.commit()
    response.delete_cookie('agraharam_session',path='/',httponly=True,secure=config.cookie_secure,samesite='lax')
    return {'ok':True}

@app.get('/users/permissions')
def permissions(user: User=Depends(admin)):return PERMISSIONS
@app.get('/users')
def users(user: User=Depends(admin),db: Session=Depends(get_db)):
    return [user_view(u) for u in db.scalars(select(User).order_by(User.created_at))]
@app.post('/users',status_code=201)
def create_user(body: UserIn,user: User=Depends(admin),db: Session=Depends(get_db)):
    initial_passcode=body.passcode
    item=User(id=str(uuid4()),username=body.username or str(body.email).split('@')[0].lower(),passcode_ciphertext=encrypt(initial_passcode) if initial_passcode else None,email=str(body.email).lower(),name=body.name,password_hash=hasher.hash(initial_passcode or body.password),is_admin=body.is_admin,active=body.active,permissions=body.permissions)
    db.add(item);audit(db,user,'user.created',item.id,{'email':item.email,'permissions':item.permissions,'is_admin':item.is_admin})
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,'That email or username already exists')
    return {**user_view(item),**({'initial_passcode':initial_passcode} if initial_passcode else {})}
@app.put('/users/{id}')
def update_user(id: str,body: UserEdit,user: User=Depends(admin),db: Session=Depends(get_db)):
    # Serialize administrator changes to keep at least one active administrator.
    if db.bind.dialect.name=='postgresql':db.execute(text('SELECT pg_advisory_xact_lock(74839201)'))
    item=get_or_404(db,User,id)
    if item.id==user.id and (not body.active or not body.is_admin):raise HTTPException(400,'You cannot disable or demote your own administrator account')
    if item.is_admin and item.active and (not body.is_admin or not body.active):
        remaining=db.scalar(select(func.count()).select_from(User).where(User.id!=item.id,User.is_admin.is_(True),User.active.is_(True)))
        if not remaining:raise HTTPException(400,'Keep at least one active administrator')
    before=user_view(item)
    if body.email:item.email=str(body.email).lower()
    if body.username:item.username=body.username
    item.name=body.name;item.is_admin=body.is_admin;item.active=body.active;item.permissions=body.permissions
    credential=body.passcode or body.password
    if credential:
        item.password_hash=hasher.hash(credential)
        item.passcode_ciphertext=encrypt(body.passcode) if body.passcode else None
        db.execute(delete(LoginSession).where(LoginSession.user_id==item.id))
    if not body.active:db.execute(delete(LoginSession).where(LoginSession.user_id==item.id))
    audit(db,user,'user.updated',item.id,{'before':before,'after':user_view(item),'credential_changed':bool(credential)})
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,'That email or username already exists')
    return user_view(item)

@app.get('/users/{id}/passcode')
def reveal_passcode(id: str,user: User=Depends(admin),db: Session=Depends(get_db)):
    item=get_or_404(db,User,id)
    value=decrypt(item.passcode_ciphertext) if item.passcode_ciphertext else None
    audit(db,user,'user.passcode_viewed',id);db.commit()
    return {'passcode':value}

@app.get('/audit')
def audits(user: User=Depends(admin),db: Session=Depends(get_db)):
    return [{'id':a.id,'actor_id':a.actor_id,'action':a.action,'target_id':a.target_id,'details':a.details,'created_at':a.created_at} for a in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]

def menu_view(i):return {'id':i.id,'name':i.name,'category':i.category,'price':float(i.price),'active':i.active,**(i.service_details or {})}
@app.get('/menu')
def menu(user: User=Depends(require('menu','billing')),db: Session=Depends(get_db)):
    q=select(MenuItem).order_by(MenuItem.category,MenuItem.name)
    if not user.is_admin and 'menu' not in user.permissions:q=q.where(MenuItem.active.is_(True))
    rate=float(settings(db).tax_rate)
    return [{**menu_view(i),'configured_gst_rate':float((i.service_details or {}).get('gst_rate_override') if (i.service_details or {}).get('gst_rate_override') is not None else rate),'gst_rate':rate} for i in db.scalars(q)]
@app.post('/menu',status_code=201)
def add_menu(body: MenuIn,user: User=Depends(require('menu')),db: Session=Depends(get_db)):
    payload=body.model_dump();details={k:payload.pop(k) for k in ('code','duration_minutes','sac_code','hsn_code','gst_rate_override','description','image')}
    if details['gst_rate_override'] is not None:details['gst_rate_override']=float(details['gst_rate_override'])
    ensure_category(db,payload['category'])
    i=MenuItem(id=str(uuid4()),service_details=details,**payload);db.add(i);audit(db,user,'menu.created',i.id,{'name':i.name,'price':str(i.price)});db.commit();return menu_view(i)
@app.put('/menu/{id}')
def edit_menu(id: str,body: MenuIn,user: User=Depends(require('menu')),db: Session=Depends(get_db)):
    i=get_or_404(db,MenuItem,id);before={k:v for k,v in menu_view(i).items() if k!='image'}
    payload=body.model_dump();i.service_details={k:payload.pop(k) for k in ('code','duration_minutes','sac_code','hsn_code','gst_rate_override','description','image')}
    if i.service_details['gst_rate_override'] is not None:i.service_details['gst_rate_override']=float(i.service_details['gst_rate_override'])
    ensure_category(db,payload['category'])
    for k,v in payload.items():setattr(i,k,v)
    audit(db,user,'menu.updated',i.id,{'before':before,'after':{k:v for k,v in menu_view(i).items() if k!='image'}});db.commit();return menu_view(i)

def ensure_category(db,name):
    category=db.scalar(select(Category).where(func.lower(Category.name)==name.lower()))
    if category and not category.active:raise HTTPException(400,'Category is inactive')
    if not category:db.add(Category(id=str(uuid4()),name=name,active=True));db.flush()

@app.get('/categories')
def categories(user: User=Depends(require('menu','billing')),db: Session=Depends(get_db)):
    query=select(Category).order_by(Category.name)
    if not user.is_admin and 'menu' not in user.permissions:query=query.where(Category.active.is_(True))
    return [{'id':c.id,'name':c.name,'active':c.active} for c in db.scalars(query)]

@app.post('/categories',status_code=201)
def create_category(body: CategoryIn,user: User=Depends(require('menu')),db: Session=Depends(get_db)):
    if db.scalar(select(Category).where(func.lower(Category.name)==body.name.lower())):raise HTTPException(409,'Category already exists')
    c=Category(id=str(uuid4()),name=body.name,active=body.active);db.add(c);audit(db,user,'category.created',c.id,{'name':c.name})
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,'Category already exists')
    return {'id':c.id,'name':c.name,'active':c.active}

def customer_view(c):return {'gstin':c.gstin,'id':c.id,'name':c.name,'phone':c.phone}
@app.get('/customers')
def customers(user: User=Depends(require('customers','billing')),db: Session=Depends(get_db)):
    return [customer_view(c) for c in db.scalars(select(Customer).order_by(Customer.name))]
@app.post('/customers',status_code=201)
def add_customer(body: CustomerIn,user: User=Depends(require('customers')),db: Session=Depends(get_db)):
    c=Customer(id=str(uuid4()),**body.model_dump());db.add(c);audit(db,user,'customer.created',c.id);db.commit();return customer_view(c)
@app.put('/customers/{id}')
def edit_customer(id: str,body: CustomerIn,user: User=Depends(require('customers')),db: Session=Depends(get_db)):
    c=get_or_404(db,Customer,id)
    for k,v in body.model_dump().items():setattr(c,k,v)
    audit(db,user,'customer.updated',c.id);db.commit();return customer_view(c)

def settings_view(s):return {'gstin':s.gstin,'state_code':s.state_code,'address':s.address,'phone':s.phone,'tax_rate':float(s.tax_rate),'business_name':s.business_name,'branch_name':s.branch_name,'legal_name':s.legal_name,'fssai_number':s.fssai_number,'invoice_prefix':s.invoice_prefix}

def invoice_number(db,s,created_at):
    local=created_at.astimezone(ZoneInfo('Asia/Kolkata'))
    year=local.year if local.month>=4 else local.year-1
    fy=f'{year%100:02d}{(year+1)%100:02d}'
    prefix=f'{s.invoice_prefix}-{fy}-'
    # Serialize invoice allocation across online and offline billing sessions.
    db.execute(text('SELECT pg_advisory_xact_lock(74120512)'))
    previous=db.scalars(select(Order.bill_number).where(Order.bill_number.like(prefix+'%'))).all()
    numbers=[int(v[len(prefix):]) for v in previous if v and v[len(prefix):].isdigit()]
    return prefix+str(max(numbers,default=0)+1)

def receipt_settings(s):
    return {k:getattr(s,k) for k in ('business_name','branch_name','legal_name','fssai_number','gstin','address','phone')}

def tax_lines(items,default_rate,seller_state,buyer_state):
    interstate=bool(buyer_state and seller_state and buyer_state!=seller_state)
    total_taxable=Decimal('0');total_tax=Decimal('0');total_cgst=Decimal('0');total_sgst=Decimal('0');total_igst=Decimal('0')
    for line in items:
        rate=Decimal(str(line.get('gst_rate_override') if line.get('gst_rate_override') is not None else default_rate))
        gross=Decimal(str(line['line_total']))
        taxable=(gross/(1+rate/100)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        tax=gross-taxable
        cgst=Decimal('0') if interstate else (tax/2).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        sgst=Decimal('0') if interstate else tax-cgst
        igst=tax if interstate else Decimal('0')
        line.update({'gst_rate':str(rate),'taxable_value':str(taxable),'tax_amount':str(tax),'cgst':str(cgst),'sgst':str(sgst),'igst':str(igst)})
        total_taxable+=taxable;total_tax+=tax;total_cgst+=cgst;total_sgst+=sgst;total_igst+=igst
    return total_taxable,total_tax,total_cgst,total_sgst,total_igst

@app.get('/settings')
def read_settings(user: User=Depends(require('settings','billing')),db: Session=Depends(get_db)):return settings_view(settings(db))
@app.put('/settings')
def edit_settings(body: SettingsIn,user: User=Depends(require('settings')),db: Session=Depends(get_db)):
    s=settings(db)
    for k,v in body.model_dump().items():setattr(s,k,v)
    audit(db,user,'settings.updated',1,settings_view(s));db.commit();return settings_view(s)

def order_view(o):return {'branch_id':o.branch_id,'gst_details':o.gst_details,'id':o.id,'status':o.status,'cancellation':o.cancellation,'bill_number':o.bill_number or f'AGR-{o.id:06d}','receipt_details':o.receipt_details or {},'request_id':o.request_id,'staff_name':o.staff_name,'staff_username':o.staff_username,'customer_name':o.customer_name,'customer_id':o.customer_id,'order_type':o.order_type,'payment':o.payment,'payments':o.payment_details or [{'method':o.payment,'amount':str(o.total)}],'items':o.items,'total':float(o.total),'subtotal':float(o.subtotal),'tax':float(o.tax),'tax_rate':float(o.tax_rate),'address':o.address,'phone':o.phone,'created_at':o.created_at}
@app.post('/orders',status_code=201)
def create_order(body: OrderIn,user: User=Depends(require('billing')),db: Session=Depends(get_db)):
    rid=str(body.request_id)
    def existing():
        old=db.scalar(select(Order).where(Order.request_id==rid))
        if old and (old.staff_id!=user.id or OrderIn.model_validate(old.request_body).model_dump(mode='json')!=body.model_dump(mode='json')):raise HTTPException(409,'Request identifier belongs to a different bill; start a new request')
        return old
    old=existing()
    if old:return order_view(old)
    branch_id = body.branch_id or (user.branch_id if not user.is_admin else 'BR002')
    if not branch_id:raise HTTPException(403,'Cashier has no assigned branch; contact administrator')
    if not user.is_admin and branch_id!=user.branch_id:raise HTTPException(403,'You cannot bill for another branch')
    branch=db.get(Branch,branch_id)
    if not branch or not branch.active:raise HTTPException(422,'Selected branch is unavailable')
    quantities={}
    for line in body.items:
        id=str(line.item_id);quantities[id]=quantities.get(id,0)+line.qty
        if quantities[id]>1000:raise HTTPException(422,'Maximum item quantity is 1000')
    items=[];total=Decimal('0')
    for id,qty in quantities.items():
        item=get_or_404(db,MenuItem,id)
        if not item.active:raise HTTPException(400,f'{item.name} is unavailable')
        line_total=item.price*qty;total+=line_total
        items.append({'item_id':id,'name':item.name,'category':item.category,'price':str(item.price),'qty':qty,'line_total':str(line_total),**{k:v for k,v in (item.service_details or {}).items() if k!='image'}})
    if total != body.expected_total:raise HTTPException(409,'Item prices changed. Reload the billing page and review the updated total before payment.')
    c=get_or_404(db,Customer,body.customer_id) if body.customer_id else None;s=settings(db)
    if not s.gstin or not (s.state_code or s.gstin[:2]):
        raise HTTPException(422,'Configure the business GSTIN and state code in Settings before creating a GST bill')
    if not body.place_of_supply:
        raise HTTPException(422,'Place of supply is required for GST billing; refresh POS settings and retry')
    if body.buyer_gstin and body.buyer_gstin[:2]!=body.place_of_supply:
        raise HTTPException(422,'Buyer GSTIN state and place of supply differ; review the transaction')
    subtotal,calculated_tax,cgst,sgst,igst=tax_lines(items,s.tax_rate,s.state_code or s.gstin[:2],body.place_of_supply)
    snapshot=tax_snapshot(s.gstin,s.state_code,body.buyer_gstin,body.place_of_supply,calculated_tax,total)
    if snapshot.get('classification')!='UNCLASSIFIED':snapshot.update(cgst=str(cgst),sgst=str(sgst),igst=str(igst))
    branch_receipt={**receipt_settings(s),'branch_name':branch.name,'invoice_prefix':branch.invoice_prefix,'address':branch.address or (s.address if branch.id=='BR002' else ''),'phone':branch.phone or (s.phone if branch.id=='BR002' else ''),'fssai_number':branch.fssai_number or s.fssai_number}
    order=Order(branch_id=branch.id,gst_details=snapshot,receipt_details=branch_receipt,request_id=rid,request_body=body.model_dump(mode='json'),staff_id=user.id,staff_name=user.name,staff_username=user.username or '',customer_id=c.id if c else None,customer_name=c.name if c else '',order_type=body.order_type,payment=body.payment,payment_details=payment_details(body),items=items,total=total,subtotal=subtotal,tax=total-subtotal,tax_rate=s.tax_rate,address=branch_receipt['address'],phone=branch_receipt['phone'])
    db.add(order)
    try:
        order.bill_number=invoice_number(db,branch,datetime.now(timezone.utc))
        db.flush();audit(db,user,'order.completed',order.id,{'total':str(total),'payment':body.payment});db.commit()
    except IntegrityError:
        db.rollback();old=existing()
        if old:return order_view(old)
        raise HTTPException(409,'Order could not be saved; retry the same request')
    return order_view(order)

@app.get('/branches')
def branches(user: User=Depends(current_user),db: Session=Depends(get_db)):
    rows=db.scalars(select(Branch).where(Branch.active.is_(True)).order_by(Branch.id)).all()
    if not user.is_admin:rows=[b for b in rows if b.id==user.branch_id]
    return [{'id':b.id,'name':b.name,'invoice_prefix':b.invoice_prefix,'gstin':b.gstin,'address':b.address,'phone':b.phone} for b in rows]

@app.put('/users/{id}/branch')
def assign_user_branch(id: str,branch_id: str,user: User=Depends(admin),db: Session=Depends(get_db)):
    branch=db.get(Branch,branch_id)
    if not branch or not branch.active:raise HTTPException(422,'Unknown or inactive branch')
    target=get_or_404(db,User,id)
    target.branch_id=branch.id
    audit(db,user,'user.branch_assigned',id,{'branch_id':branch.id})
    db.commit()
    return {'id':target.id,'name':target.name,'branch_id':target.branch_id}

def resolved_branch(user,branch_id,db):
    if user.is_admin:
        if branch_id and (not db.get(Branch,branch_id) or not db.get(Branch,branch_id).active):
            raise HTTPException(422,'Unknown or inactive branch')
        return branch_id
    if not user.branch_id:raise HTTPException(403,'Cashier branch is not assigned')
    if branch_id and branch_id!=user.branch_id:raise HTTPException(403,'Branch access denied')
    return user.branch_id

@app.get('/orders/cashiers')
def order_cashiers(branch_id: str|None=None,user: User=Depends(require('orders')),db: Session=Depends(get_db)):
    selected=resolved_branch(user,branch_id,db)
    q=select(Order.staff_name).distinct()
    if selected:q=q.where(Order.branch_id==selected)
    return sorted([name for name in db.scalars(q) if name],key=str.casefold)
@app.get('/orders')
def orders(q: str=Query('',max_length=100),payment: str|None=None,start: date|None=None,end: date|None=None,cashier: str|None=None,branch_id: str|None=None,limit: int=Query(100,ge=1,le=500),offset: int=Query(0,ge=0),user: User=Depends(require('orders')),db: Session=Depends(get_db)):
    stmt=select(Order)
    selected=resolved_branch(user,branch_id,db)
    if selected:stmt=stmt.where(Order.branch_id==selected)
    if q:
        stmt=stmt.where((Order.bill_number==q)|(Order.customer_name.ilike('%'+q.replace('%','\\%').replace('_','\\_')+'%',escape='\\'))|(Order.id==int(q.removeprefix('AGR-')) if q.removeprefix('AGR-').isdigit() else False))
    if payment:stmt=stmt.where(or_(Order.payment==payment,*[Order.payment_details[n]['method'].as_string()==payment for n in range(3)]))
    if start and end and start>end:raise HTTPException(422,'End date must follow start date')
    if start:stmt=stmt.where(Order.created_at>=datetime.combine(start,time.min,tzinfo=ZoneInfo('Asia/Kolkata')).astimezone(timezone.utc))
    if end:stmt=stmt.where(Order.created_at<datetime.combine(end+timedelta(days=1),time.min,tzinfo=ZoneInfo('Asia/Kolkata')).astimezone(timezone.utc))
    if cashier:stmt=stmt.where(Order.staff_name==cashier)
    count=db.scalar(select(func.count()).select_from(stmt.subquery()))
    return {'total':count,'items':[order_view(o) for o in db.scalars(stmt.order_by(Order.id.desc()).limit(limit).offset(offset))]}
@app.get('/orders/{id}')
def receipt(id: int,user: User=Depends(require('orders','billing')),db: Session=Depends(get_db)):
    o=db.get(Order,id)
    if not o:raise HTTPException(404,'Bill not found')
    resolved_branch(user,o.branch_id,db)
    if not user.is_admin and 'orders' not in user.permissions and o.staff_id!=user.id:raise HTTPException(403,'Access denied')
    return order_view(o)

@app.post('/orders/{id}/cancel')
def cancel_bill(id: int,body: CancelIn,user: User=Depends(require('cancel_bills')),db: Session=Depends(get_db)):
    order=db.scalar(select(Order).where(Order.id==id).with_for_update())
    if not order:raise HTTPException(404,'Bill not found')
    resolved_branch(user,order.branch_id,db)
    if order.status=='Cancelled':return order_view(order)
    order.status='Cancelled'
    order.cancellation={'reason':body.reason,'actor_id':user.id,'actor_name':user.name,'cancelled_at':datetime.now(timezone.utc).isoformat()}
    audit(db,user,'bill.cancelled',order.id,{'reason':body.reason,'total':str(order.total),'payment':order.payment})
    db.commit();return order_view(order)

IST=ZoneInfo('Asia/Kolkata')
def date_query(start,end,include_cancelled=False):
    if start and end and start>end:raise HTTPException(422,'End date must follow start date')
    q=select(Order)
    if not include_cancelled:q=q.where(Order.status=='Completed')
    if start:q=q.where(Order.created_at>=datetime.combine(start,time.min,tzinfo=IST).astimezone(timezone.utc))
    if end:q=q.where(Order.created_at<datetime.combine(end+timedelta(days=1),time.min,tzinfo=IST).astimezone(timezone.utc))
    return q

def summary(db,start,end):
    rows=list(db.scalars(date_query(start,end)));payments={p:Decimal('0') for p in ['Cash','UPI','Card']};popular={}
    for o in rows:
        for method,amount in allocations(o).items():payments[method]+=amount
        for i in o.items:
            p=popular.setdefault(i['name'],{'name':i['name'],'qty':0,'sales':0.0});p['qty']+=i['qty'];p['sales']+=float(i['line_total'])
    return {'total_sales':float(sum((o.total for o in rows),Decimal('0'))),'bill_count':len(rows),'tax':float(sum((o.tax for o in rows),Decimal('0'))),'cancelled_count':db.scalar(select(func.count()).select_from(date_query(start,end,True).where(Order.status=='Cancelled').subquery())),'cancelled_amount':float(sum((o.total for o in db.scalars(date_query(start,end,True).where(Order.status=='Cancelled'))),Decimal('0'))),'payments':{k:float(v) for k,v in payments.items()},'popular':sorted(popular.values(),key=lambda i:i['sales'],reverse=True)}
@app.get('/dashboard')
def dashboard(user: User=Depends(require('dashboard')),db: Session=Depends(get_db)):
    day=datetime.now(IST).date();return summary(db,day,day)
@app.get('/reports')
def reports(start: date|None=None,end: date|None=None,user: User=Depends(require('reports','report_pos')),db: Session=Depends(get_db)):return summary(db,start,end)
@app.get('/reports/export')
def export(start: date|None=None,end: date|None=None,user: User=Depends(require('reports','report_pos')),db: Session=Depends(get_db)):
    s=io.StringIO();w=csv.writer(s);w.writerow(['Bill','Date UTC','Customer','Staff','Type','Payment','Subtotal','Tax','Total','Cash','UPI','Card'])
    def safe(v):
        v=str(v)
        return "'"+v if v.lstrip().startswith(('=','+','-','@','\t','\r')) else v
    for o in db.scalars(date_query(start,end).order_by(Order.id)):
        w.writerow([f'AGR-{o.id:06d}',o.created_at.isoformat(),safe(o.customer_name),safe(o.staff_name),o.order_type,o.payment,o.subtotal,o.tax,o.total,*allocations(o).values()])
    return Response('\ufeff'+s.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="agraharam-sales.csv"'})


def service_summary(db,start,end):
    grouped={}
    for o in db.scalars(date_query(start,end).order_by(Order.id)):
        # Allocate stored bill tax across lines; the final line receives the rounding remainder.
        # Historic snapshots keep reports stable when services or tax settings change.
        remaining=o.tax;payment_lines=line_allocations(o)
        for index,line in enumerate(o.items):
            total=Decimal(str(line['line_total']))
            tax=remaining if index==len(o.items)-1 else (o.tax*total/o.total).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            remaining-=tax
            key=line['item_id']+'|'+line['name']
            row=grouped.setdefault(key,{'id':key,'name':line['name'],'qty':0,'subtotal':Decimal('0'),'tax':Decimal('0'),'total':Decimal('0'),'Cash':Decimal('0'),'UPI':Decimal('0'),'Card':Decimal('0')})
            row['qty']+=line['qty'];row['subtotal']+=total-tax;row['tax']+=tax;row['total']+=total
            for method,amount in payment_lines[index].items():row[method]+=amount
    rows=sorted(grouped.values(),key=lambda r:r['total'],reverse=True)
    totals={k:sum((r[k] for r in rows),0 if k=='qty' else Decimal('0')) for k in ('qty','subtotal','tax','total','Cash','UPI','Card')}
    return {'items':rows,'totals':totals}

@app.get('/reports/services')
def service_report(start: date|None=None,end: date|None=None,user: User=Depends(require('reports','report_services')),db: Session=Depends(get_db)):
    return service_summary(db,start,end)

@app.get('/reports/services/export')
def service_export(start: date|None=None,end: date|None=None,user: User=Depends(require('reports','report_services')),db: Session=Depends(get_db)):
    result=service_summary(db,start,end);s=io.StringIO();w=csv.writer(s)
    w.writerow(['Item','Qty sold','Sales before tax','Included tax','Total collections','Cash','UPI','Card'])
    for r in result['items']:
        name=r['name']
        if name.lstrip().startswith(('=','+','-','@','\t','\r')):name="'"+name
        w.writerow([name,r['qty'],r['subtotal'],r['tax'],r['total'],r['Cash'],r['UPI'],r['Card']])
    t=result['totals'];w.writerow(['TOTAL',t['qty'],t['subtotal'],t['tax'],t['total'],t['Cash'],t['UPI'],t['Card']])
    return Response('\ufeff'+s.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="agraharam-item-sales.csv"'})

def gst_summary(db,start,end):
    grouped={}
    for o in db.scalars(date_query(start,end)):
        key=str(o.tax_rate)
        row=grouped.setdefault(key,{'rate':o.tax_rate,'bills':0,'subtotal':Decimal('0'),'tax':Decimal('0'),'total':Decimal('0')})
        row['bills']+=1
        for k in ('subtotal','tax','total'):row[k]+=getattr(o,k)
    rows=sorted(grouped.values(),key=lambda r:r['rate'])
    return {'items':rows,'totals':{k:sum((r[k] for r in rows),0) for k in ('bills','subtotal','tax','total')}}

@app.get('/reports/gst')
def gst_report(start: date|None=None,end: date|None=None,user: User=Depends(require('reports','report_gst')),db: Session=Depends(get_db)):
    rows=list(db.scalars(date_query(start,end,True).order_by(Order.id)))
    return {**gst_summary(db,start,end),**gst_report_data(rows)}

@app.get('/reports/gst/export')
def gst_export(start: date|None=None,end: date|None=None,user: User=Depends(require('reports','report_gst')),db: Session=Depends(get_db)):
    result=gst_report_data(list(db.scalars(date_query(start,end,True).order_by(Order.id))));s=io.StringIO();w=csv.writer(s)
    w.writerow(['Type','Taxable value','CGST','SGST','IGST','CESS','Total amount','Status'])
    for r in result['sections']:w.writerow([r[k] for k in ('type','subtotal','cgst','sgst','igst','cess','total','note')])
    return Response('\ufeff'+s.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="agraharam-gst.csv"'})

from .offline import router as offline_router
app.include_router(offline_router)



if config.frontend_dir:
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    if not (Path(config.frontend_dir)/'index.html').is_file():raise RuntimeError('FRONTEND_DIR must point to the built frontend dist directory')
    app.mount('/',StaticFiles(directory=config.frontend_dir,html=True),name='installed-app')
