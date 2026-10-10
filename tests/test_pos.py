import os
from pathlib import Path
from uuid import uuid4
os.environ.setdefault('APP_ENV','test')
os.environ.setdefault('DATABASE_URL','sqlite:////tmp/agraharam-pytest.db')
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import Base,engine,SessionLocal
from app.models import User,Order
from app.seed import seed
from app.security import hasher
import pytest
ORIGIN={'Origin':'http://localhost:5181'}
@pytest.fixture(autouse=True)
def fresh():
    Base.metadata.drop_all(engine);seed()
    yield
    Base.metadata.drop_all(engine)
def login(c,email='support@kalaguragampa.com',password='87654321'):
    return c.post('/auth/login',json={'email':email,'password':password},headers=ORIGIN)
def admin_client():
    c=TestClient(app);assert login(c).status_code==200;return c
def create_staff(c,permissions):
    r=c.post('/users',json={'email':'cashier@example.com','name':'Cashier','password':'strong-test-123','permissions':permissions},headers=ORIGIN)
    assert r.status_code==201;return r.json()
def item(c,price=40):
    r=c.post('/menu',json={'name':'Idli','category':'Tiffins','price':price},headers=ORIGIN);assert r.status_code==201;return r.json()
def bill(i,**updates):
    return {'request_id':str(uuid4()),'expected_total':'80.00','order_type':'Takeaway','payment':'Cash','items':[{'item_id':i['id'],'qty':2}],**updates}
def test_admin_login_password_hash_and_cookie():
    c=admin_client();assert c.get('/auth/me').json()['is_admin'];assert 'password_hash' not in c.get('/users').text
    with SessionLocal() as db:
        u=db.scalar(select(User));assert u.password_hash!='87654321';assert hasher.verify('87654321',u.password_hash)
    response=login(TestClient(app));assert 'HttpOnly' in response.headers['set-cookie'];assert 'SameSite=lax' in response.headers['set-cookie']
    assert c.post('/auth/logout',json={},headers=ORIGIN).status_code==200;assert c.get('/auth/me').status_code==401

def test_staff_server_side_permissions_and_revocation():
    a=admin_client();u=create_staff(a,['billing']);i=item(a)
    s=TestClient(app);assert login(s,'cashier@example.com','strong-test-123').status_code==200
    assert s.get('/menu').status_code==200;assert s.get('/settings').status_code==200
    for path in ['/users','/audit','/reports','/orders','/dashboard']:assert s.get(path).status_code==403
    assert s.post('/menu',json={'name':'X','category':'Meals','price':1},headers=ORIGIN).status_code==403
    assert s.post('/users',json={'email':'hack@example.com','name':'Hack','password':'password123','is_admin':True},headers=ORIGIN).status_code==403
    assert s.post('/orders',json=bill(i),headers=ORIGIN).status_code==201
    r=a.put('/users/'+u['id'],json={'name':'Cashier','is_admin':False,'active':True,'permissions':['customers']},headers=ORIGIN);assert r.status_code==200
    assert s.get('/menu').status_code==403;assert s.get('/customers').status_code==200
    assert s.post('/orders',json=bill(i),headers=ORIGIN).status_code==403
    a.put('/users/'+u['id'],json={'name':'Cashier','is_admin':False,'active':False,'permissions':[]},headers=ORIGIN)
    assert s.get('/auth/me').status_code==401

def test_billing_price_snapshots_tax_and_retry():
    a=admin_client();i=item(a);a.put('/settings',json={'tax_rate':5,'address':'Agraharam'},headers=ORIGIN)
    payload=bill(i);r=a.post('/orders',json=payload,headers=ORIGIN);assert r.status_code==201;o=r.json()
    assert o['total']==80;assert o['subtotal']==76.19;assert o['tax']==3.81
    duplicate=a.post('/orders',json=payload,headers=ORIGIN);assert duplicate.json()['id']==o['id']
    assert a.get('/orders').json()['total']==1
    assert a.post('/orders',json={**payload,'payment':'UPI'},headers=ORIGIN).status_code==409
    a.put('/menu/'+i['id'],json={'name':'Idli updated','category':'Tiffins','price':50},headers=ORIGIN)
    saved=a.get('/orders/'+str(o['id'])).json();assert saved['items'][0]['name']=='Idli';assert saved['total']==80
    assert a.post('/orders',json=bill(i),headers=ORIGIN).status_code==409
    assert a.post('/orders',json=bill(i,expected_total=100,total=1),headers=ORIGIN).status_code==422
    report=a.get('/reports').json();assert report['bill_count']==1;assert report['payments']['Cash']==80
    assert 'AGR-000001' in a.get('/reports/export').text

def test_invalid_grants_duplicate_email_self_protection_and_csrf():
    a=admin_client();u=create_staff(a,['billing'])
    assert a.post('/users',json={'email':'cashier@example.com','name':'Copy','password':'password123'},headers=ORIGIN).status_code==409
    assert a.post('/users',json={'email':'bad@example.com','name':'Bad','password':'password123','permissions':['godmode']},headers=ORIGIN).status_code==422
    own=a.get('/auth/me').json()
    assert a.put('/users/'+own['id'],json={'name':'Admin','is_admin':False,'active':False,'permissions':[]},headers=ORIGIN).status_code==400
    assert a.post('/menu',json={'name':'Test','category':'Meals','price':1}).status_code==403
    assert a.post('/menu',json={'name':'Test','category':'Meals','price':1},headers={'Origin':'https://evil.example'}).status_code==403
    assert a.get('/users').status_code==200

def test_login_rate_limit_and_password_reset_revoke_session():
    a=admin_client();u=create_staff(a,['billing']);s=TestClient(app);login(s,'cashier@example.com','strong-test-123')
    r=a.put('/users/'+u['id'],json={'name':'Cashier','active':True,'is_admin':False,'permissions':['billing'],'password':'new-test-password'},headers=ORIGIN);assert r.status_code==200
    assert s.get('/auth/me').status_code==401;assert login(s,'cashier@example.com','new-test-password').status_code==200
    c=TestClient(app)
    for _ in range(5):assert login(c,'unknown@example.com','wrong-pass').status_code==401
    assert login(c,'unknown@example.com','wrong-pass').status_code==429

def test_inactive_menu_and_billing_only_receipt_scope():
    a=admin_client();i=item(a);u=create_staff(a,['billing']);admin_order=a.post('/orders',json=bill(i),headers=ORIGIN).json()
    s=TestClient(app);login(s,'cashier@example.com','strong-test-123')
    assert s.get('/orders/'+str(admin_order['id'])).status_code==403
    a.put('/menu/'+i['id'],json={'name':'Idli','category':'Tiffins','price':40,'active':False},headers=ORIGIN)
    assert not s.get('/menu').json();assert s.post('/orders',json=bill(i),headers=ORIGIN).status_code==400


def test_service_billing_report_snapshot_and_access():
    a=admin_client()
    one=a.post('/menu',json={'name':'Session A','category':'Services','price':'10.01'},headers=ORIGIN).json()
    two=a.post('/menu',json={'name':'Session B','category':'Services','price':'20.02'},headers=ORIGIN).json()
    a.put('/settings',json={'tax_rate':7},headers=ORIGIN)
    payload=bill(one,order_type='Service',expected_total='30.03',items=[{'item_id':one['id'],'qty':1},{'item_id':two['id'],'qty':1}])
    response=a.post('/orders',json=payload,headers=ORIGIN)
    assert response.status_code==201
    order=response.json()
    assert order['order_type']=='Service'
    report=a.get('/reports/services').json()
    from decimal import Decimal
    assert len(report['items'])==2
    assert Decimal(str(report['totals']['total']))==Decimal('30.03')
    assert Decimal(str(report['totals']['tax']))==Decimal(str(order['tax']))
    assert sum(Decimal(str(r['tax'])) for r in report['items'])==Decimal(str(order['tax']))
    a.put('/menu/'+one['id'],json={'name':'Renamed','category':'Services','price':99},headers=ORIGIN)
    assert 'Session A' in [r['name'] for r in a.get('/reports/services').json()['items']]
    assert 'TOTAL' in a.get('/reports/services/export').text
    assert a.get('/reports/services?start=2030-01-01&end=2020-01-01').status_code==422
    create_staff(a,['billing']);staff=TestClient(app);login(staff,'cashier@example.com','strong-test-123')
    assert staff.get('/reports/services').status_code==403
    assert staff.get('/reports/services/export').status_code==403


def test_service_details_five_percent_and_submenu_permissions():
    a=admin_client()
    data={'name':'Consultation','category':'Services','code':'AGR-SRV-001','duration_minutes':60,'sac_code':'999723','description':'Session description','price':'105.00'}
    response=a.post('/menu',json=data,headers=ORIGIN)
    assert response.status_code==201
    service=response.json()
    for key in ('code','duration_minutes','sac_code','description'):assert service[key]==data[key]
    assert a.get('/settings').json()['tax_rate']==5
    saved=a.post('/orders',json=bill(service,order_type='Service',expected_total='105.00',items=[{'item_id':service['id'],'qty':1}]),headers=ORIGIN).json()
    assert saved['total']==105 and saved['subtotal']==100 and saved['tax']==5
    assert saved['items'][0]['code']=='AGR-SRV-001'
    staff=create_staff(a,['report_services']);c=TestClient(app);login(c,'cashier@example.com','strong-test-123')
    assert c.get('/reports/services').status_code==200
    assert c.get('/reports/services/export').status_code==200
    assert c.get('/reports').status_code==403
    assert c.get('/reports/export').status_code==403
    a.put('/users/'+staff['id'],json={'name':'Cashier','permissions':['report_pos']},headers=ORIGIN)
    assert c.get('/reports').status_code==200
    assert c.get('/reports/services').status_code==403
    assert c.get('/users').status_code==403 and c.get('/audit').status_code==403


def test_additive_service_metadata_upgrade_preserves_legacy_catalogue():
    from sqlalchemy import text
    from app.models import MenuItem
    ident=str(uuid4())
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE menu_items'))
        conn.execute(text('CREATE TABLE menu_items (id VARCHAR(36) PRIMARY KEY, name VARCHAR(80) NOT NULL, category VARCHAR(40) NOT NULL, price NUMERIC(12,2) NOT NULL, active BOOLEAN NOT NULL)'))
        conn.execute(text('INSERT INTO menu_items (id,name,category,price,active) VALUES (:id,:name,:category,:price,:active)'),{'id':ident,'name':'Existing service','category':'Services','price':105,'active':True})
    seed();seed()
    with SessionLocal() as db:
        row=db.get(MenuItem,ident)
        assert row.name=='Existing service' and row.price==105 and row.service_details=={}


def test_manual_passcode_hash_and_reset_revokes_sessions():
    a=admin_client()
    response=a.post('/users',json={'name':'Passcode Staff','email':'pin@example.com','passcode':'0042','permissions':['billing']},headers=ORIGIN)
    assert response.status_code==201
    user=response.json();pin=user['initial_passcode'];assert len(pin)==4 and pin.isdigit()
    c=TestClient(app);assert login(c,'pin@example.com',pin).status_code==200
    assert 'initial_passcode' not in c.get('/auth/me').json()
    assert 'initial_passcode' not in a.get('/users').text
    with SessionLocal() as db:
        saved=db.get(User,user['id']);assert saved.password_hash!=pin and hasher.verify(pin,saved.password_hash)
    response=a.put('/users/'+user['id'],json={'name':'Passcode Staff','permissions':['billing'],'passcode':'4567'},headers=ORIGIN)
    assert response.status_code==200 and c.get('/auth/me').status_code==401
    assert login(TestClient(app),'pin@example.com','4567').status_code==200
    assert '4567' not in a.get('/audit').text
    assert a.post('/users',json={'name':'Bad','email':'badpin@example.com','passcode':'123'},headers=ORIGIN).status_code==422


def test_categories_and_item_gst():
    a=admin_client()
    response=a.post('/categories',json={'name':'Breakfast'},headers=ORIGIN);assert response.status_code==201
    assert a.post('/categories',json={'name':'breakfast'},headers=ORIGIN).status_code==409
    i=a.post('/menu',json={'name':'Poori','category':'Breakfast','code':'AGR-POORI','price':105},headers=ORIGIN).json()
    assert a.get('/menu').json()[0]['gst_rate']==5
    assert 'Breakfast' in [c['name'] for c in a.get('/categories').json()]
    create_staff(a,['billing']);c=TestClient(app);login(c,'cashier@example.com','strong-test-123')
    assert c.get('/categories').status_code==200
    assert c.post('/categories',json={'name':'Hack'},headers=ORIGIN).status_code==403


def test_cancellation_preserves_bill_and_excludes_all_reports_and_exports():
    a=admin_client();i=item(a,105)
    orders=[]
    for payment in ('Cash','UPI','Card'):
        payload=bill(i,expected_total='105.00',items=[{'item_id':i['id'],'qty':1}],payment=payment)
        orders.append(a.post('/orders',json=payload,headers=ORIGIN).json())
    assert a.get('/reports').json()['payments']=={'Cash':105,'UPI':105,'Card':105}
    totals=a.get('/reports/services').json()['totals']
    assert all(float(totals[k])==105 for k in ('Cash','UPI','Card'))
    staff=create_staff(a,['orders']);c=TestClient(app);login(c,'cashier@example.com','strong-test-123')
    ident=orders[1]['id']
    assert c.post(f'/orders/{ident}/cancel',json={'reason':'Duplicate'},headers=ORIGIN).status_code==403
    assert a.post(f'/orders/{ident}/cancel',json={'reason':'x'},headers=ORIGIN).status_code==422
    result=a.post(f'/orders/{ident}/cancel',json={'reason':'Duplicate bill'},headers=ORIGIN)
    assert result.status_code==200 and result.json()['status']=='Cancelled'
    assert result.json()['cancellation']['reason']=='Duplicate bill'
    assert a.post(f'/orders/{ident}/cancel',json={'reason':'Second try'},headers=ORIGIN).json()['cancellation']['reason']=='Duplicate bill'
    assert a.get('/orders').json()['total']==3
    report=a.get('/reports').json();assert report['bill_count']==2 and report['cancelled_count']==1
    assert report['payments']=={'Cash':105,'UPI':0,'Card':105}
    totals=a.get('/reports/services').json()['totals'];assert float(totals['total'])==210 and float(totals['UPI'])==0
    assert orders[1]['bill_number'] not in a.get('/reports/export').text
    assert '210.00' in a.get('/reports/services/export').text
    with SessionLocal() as db:
        saved=db.get(Order,ident);assert saved.total==105 and saved.items[0]['name']=='Idli'


def test_offline_signed_snapshot_preserves_prices_tax_time_and_idempotency():
    from datetime import datetime,timezone
    a=admin_client();i=item(a,105)
    cached=a.get('/offline/catalogue');assert cached.status_code==200
    token=cached.json()['catalogue_token']
    created=datetime.now(timezone.utc).isoformat()
    order=bill(i,order_type='Item',expected_total='105.00',items=[{'item_id':i['id'],'qty':1}])
    a.put('/menu/'+i['id'],json={'name':'Changed','category':'Changed','price':150},headers=ORIGIN)
    a.put('/settings',json={'tax_rate':18},headers=ORIGIN)
    payload={'order':order,'catalogue_token':token,'created_at':created}
    response=a.post('/offline/orders',json=payload,headers=ORIGIN)
    assert response.status_code==201
    synced=response.json();assert synced['total']==105 and synced['tax']==5 and synced['subtotal']==100
    assert synced['items'][0]['name']=='Idli' and synced['tax_rate']==5
    assert a.post('/offline/orders',json=payload,headers=ORIGIN).json()['id']==synced['id']
    assert a.get('/orders').json()['total']==1
    assert a.get('/api/health').json()['database']=='connected'
    assert a.get('/api/offline/catalogue').status_code==200


def test_offline_tampering_expiry_and_permissions():
    from datetime import datetime,timezone,timedelta
    a=admin_client();i=item(a,105);cached=a.get('/offline/catalogue').json()
    payload={'order':bill(i,order_type='Item',expected_total='105.00',items=[{'item_id':i['id'],'qty':1}]),'catalogue_token':cached['catalogue_token'],'created_at':datetime.now(timezone.utc).isoformat()}
    assert a.post('/offline/orders',json={**payload,'catalogue_token':cached['catalogue_token'][:-1]+'x'},headers=ORIGIN).status_code==422
    assert a.post('/offline/orders',json={**payload,'order':{**payload['order'],'expected_total':'1.00'}},headers=ORIGIN).status_code==409
    assert a.post('/offline/orders',json={**payload,'created_at':(datetime.now(timezone.utc)+timedelta(days=4)).isoformat()},headers=ORIGIN).status_code==409
    staff=create_staff(a,['billing']);c=TestClient(app);login(c,'cashier@example.com','strong-test-123')
    assert c.post('/offline/orders',json=payload,headers=ORIGIN).status_code==403
    own=c.get('/offline/catalogue').json()
    a.put('/users/'+staff['id'],json={'name':'Cashier','permissions':['customers']},headers=ORIGIN)
    assert c.post('/offline/orders',json={**payload,'catalogue_token':own['catalogue_token']},headers=ORIGIN).status_code==403
    assert c.get('/offline/catalogue').status_code==403
