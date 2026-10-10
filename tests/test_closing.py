import pytest
pytestmark=pytest.mark.skip(reason='Daily Closing was removed at the user request; old data is retained.')
from uuid import uuid4
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from test_pos import fresh,admin_client,item,bill,ORIGIN,create_staff,login
from fastapi.testclient import TestClient
from app.main import app

def day():return datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat()
def route(s=''):return '/closing/'+day()+s
def movement(a,kind,amount,**extra):
    return a.post(route('/movements'),json={'request_id':str(uuid4()),'kind':kind,'amount':amount,'reason':'Test adjustment',**extra},headers=ORIGIN)
def close(a,report,count,notes=''):
    return a.post(route('/close'),json={'counted_cash':count,'fingerprint':report['fingerprint'],'all_counters_synced':True,'notes':notes},headers=ORIGIN)
def test_cash_reconciliation_refunds_and_audit():
    a=admin_client();i=item(a,105)
    assert a.post(route('/open'),json={'opening_cash':100},headers=ORIGIN).status_code==200
    o=a.post('/orders',json=bill(i,expected_total=210),headers=ORIGIN).json()
    a.post('/orders',json=bill(i,expected_total=210,payment='UPI'),headers=ORIGIN)
    assert movement(a,'Expense',10).status_code==201
    assert movement(a,'Cash in',20).status_code==201
    assert movement(a,'Cash out',5).status_code==201
    assert movement(a,'Refund',50,bill_id=o['id']).status_code==422
    a.post('/orders/'+str(o['id'])+'/cancel',json={'reason':'Customer returned'},headers=ORIGIN)
    assert movement(a,'Refund',210,bill_id=o['id']).status_code==201
    assert movement(a,'Refund',1,bill_id=o['id']).status_code==409
    r=a.get(route()).json()
    assert r['expected_cash']==105 and r['collections']=={'Cash':210,'UPI':210,'Card':0}
    assert r['completed_sales']==210 and r['cancelled_collections']['Cash']==210
    assert r['staff'][0]['username']=='support'
    assert close(a,r,105).status_code==200
    r=a.get(route()).json();assert r['status']=='Closed' and r['closing']['difference']=='0.00'
    assert movement(a,'Expense',1).status_code==409
    assert a.post(route('/reopen'),json={'reason':'Correct expense'},headers=ORIGIN).status_code==200
    r=a.get(route()).json();assert len(r['history'])==1 and r['history'][0]['closing']['counted_cash']=='105'
    assert 'cash.closed' in a.get('/audit').text

def test_closing_stale_totals_late_bills_and_idempotency():
    a=admin_client();i=item(a,40)
    a.post(route('/open'),json={'opening_cash':0},headers=ORIGIN)
    initial=a.get(route()).json()
    o=a.post('/orders',json=bill(i),headers=ORIGIN).json()
    assert o['staff_username']=='support' and o['staff_name']=='Agraharam Administrator'
    assert close(a,initial,80).status_code==409
    r=a.get(route()).json();assert close(a,r,70).status_code==422
    assert close(a,r,70,'Cash shortage found').status_code==200
    assert close(a,r,70,'Cash shortage found').status_code==200
    a.post('/orders',json=bill(i,payment='Card'),headers=ORIGIN)
    r=a.get(route()).json();assert r['changed_after_close']
    assert r['closing']['counted_cash']=='70' and r['closing']['bill_count']==1
    tomorrow=(datetime.now(ZoneInfo('Asia/Kolkata')).date()+timedelta(days=1)).isoformat()
    assert a.post('/closing/'+tomorrow+'/open',json={'opening_cash':0},headers=ORIGIN).status_code==422

def test_movement_retry_permissions_and_admin_void():
    a=admin_client();u=create_staff(a,['billing']);s=TestClient(app);login(s,'cashier@example.com','strong-test-123')
    assert s.get(route()).status_code==403
    a.put('/users/'+u['id'],json={'name':'Cashier','permissions':['billing','closing']},headers=ORIGIN)
    assert s.post(route('/open'),json={'opening_cash':50},headers=ORIGIN).status_code==200
    payload={'request_id':str(uuid4()),'kind':'Expense','amount':5,'reason':'Tea expense'}
    assert s.post(route('/movements'),json=payload,headers=ORIGIN).status_code==201
    assert s.post(route('/movements'),json=payload,headers=ORIGIN).status_code==201
    r=s.get(route()).json();assert len(r['movements'])==1 and r['expected_cash']==45
    assert s.post(route('/movements'),json={**payload,'amount':10},headers=ORIGIN).status_code==409
    mid=r['movements'][0]['id']
    assert s.post(route('/movements/'+str(mid)+'/void'),json={'reason':'Incorrect entry'},headers=ORIGIN).status_code==403
    assert a.post(route('/movements/'+str(mid)+'/void'),json={'reason':'Incorrect entry'},headers=ORIGIN).status_code==200
    assert s.get(route()).json()['expected_cash']==50
    assert a.post(route('/open'),json={'opening_cash':99},headers=ORIGIN).status_code==409
    r=s.get(route()).json();close(s,r,50)
    assert s.post(route('/reopen'),json={'reason':'Need correction'},headers=ORIGIN).status_code==403

def test_late_offline_bill_preserves_identity_and_closing_snapshot():
    from datetime import timezone
    a=admin_client();i=item(a,40)
    snapshot=a.get('/offline/catalogue').json();created=datetime.now(timezone.utc).isoformat()
    a.post(route('/open'),json={'opening_cash':0},headers=ORIGIN)
    r=a.get(route()).json();assert close(a,r,0).status_code==200
    user=a.get('/auth/me').json()
    assert a.put('/users/'+user['id'],json={'name':'Updated Admin Name','username':'supportnew','is_admin':True,'permissions':[]},headers=ORIGIN).status_code==200
    payload={'order':bill(i),'created_at':created,'catalogue_token':snapshot['catalogue_token']}
    response=a.post('/offline/orders',json=payload,headers=ORIGIN);assert response.status_code==201
    saved=response.json();assert saved['staff_name']=='Agraharam Administrator' and saved['staff_username']=='support'
    r=a.get(route()).json();assert r['changed_after_close'] and r['expected_cash']==80
    assert r['closing']['counted_cash']=='0' and r['closing']['bill_count']==0
