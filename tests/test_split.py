from decimal import Decimal
from types import SimpleNamespace
from datetime import datetime, timezone
import pytest
from test_pos import fresh, admin_client, item, bill, ORIGIN
from app.payments import line_allocations

def split_bill(i):
    return bill(i,payment='Split',payments=[{'method':'Cash','amount':'30.00'},{'method':'UPI','amount':'40.00'},{'method':'Card','amount':'10.00'}])

def test_split_reports_filters_retry_and_cancellation():
    c=admin_client();i=item(c);payload=split_bill(i)
    r=c.post('/orders',json=payload,headers=ORIGIN);assert r.status_code==201,r.text
    saved=r.json();assert saved['payment']=='Split'
    assert {p['method']:Decimal(str(p['amount'])) for p in saved['payments']}=={'Cash':30,'UPI':40,'Card':10}
    assert c.post('/orders',json=payload,headers=ORIGIN).json()['id']==saved['id']
    assert c.get('/reports').json()['payments']=={'Cash':30,'UPI':40,'Card':10}
    for method in ['Cash','UPI','Card','Split']:
        assert c.get('/orders',params={'payment':method}).json()['total']==1
    totals=c.get('/reports/services').json()['totals']
    assert [totals[k] for k in ['Cash','UPI','Card']]==[30,40,10]
    assert 'Cash,UPI,Card' in c.get('/reports/export').text
    assert c.post('/orders/'+str(saved['id'])+'/cancel',json={'reason':'Customer cancelled'},headers=ORIGIN).status_code==200
    assert c.get('/reports').json()['payments']=={'Cash':0,'UPI':0,'Card':0}
    assert c.get('/orders/'+str(saved['id'])).json()['payments']==saved['payments']

@pytest.mark.parametrize('parts',[
    [('Cash',30),('UPI',40)], [('Cash',40),('UPI',41)],
    [('Cash',40),('Cash',40)], [('Cash',0),('UPI',80)],
    [('Cash',-1),('UPI',81)], [('Cash',80)],
])
def test_invalid_split_rejected(parts):
    c=admin_client();i=item(c)
    payload=split_bill(i);payload['payments']=[{'method':m,'amount':v} for m,v in parts]
    assert c.post('/orders',json=payload,headers=ORIGIN).status_code==422
    assert c.get('/orders').json()['total']==0

def test_payment_rounding_conserves_lines_and_methods():
    for a in range(1,18):
        for b in range(1,18):
            total=a+b
            order=SimpleNamespace(items=[{'line_total':str(Decimal(a)/100)},{'line_total':str(Decimal(b)/100)}],payment_details=[{'method':'Cash','amount':str(Decimal(total//2)/100)},{'method':'UPI','amount':str(Decimal(total-total//2)/100)}])
            rows=line_allocations(order)
            assert [sum(r.values()) for r in rows]==[Decimal(a)/100,Decimal(b)/100]
            assert sum(r['Cash'] for r in rows)==Decimal(total//2)/100
            assert sum(r['UPI'] for r in rows)==Decimal(total-total//2)/100
            assert all(v>=0 for r in rows for v in r.values())

def test_daily_closing_removed():
    c=admin_client()
    assert c.get('/closing/day').status_code==404
    assert c.post('/closing/day',json={},headers=ORIGIN).status_code==404

def test_offline_split_sync_and_retry():
    c=admin_client();i=item(c)
    token=c.get('/offline/catalogue').json()['catalogue_token']
    payload={'order':split_bill(i),'catalogue_token':token,'created_at':datetime.now(timezone.utc).isoformat()}
    r=c.post('/offline/orders',json=payload,headers=ORIGIN);assert r.status_code==201,r.text
    saved=r.json()
    assert [Decimal(str(p['amount'])) for p in saved['payments']]==[30,40,10]
    assert c.post('/offline/orders',json=payload,headers=ORIGIN).json()['id']==saved['id']
    assert c.get('/orders').json()['total']==1
    assert c.get('/reports').json()['payments']=={'Cash':30,'UPI':40,'Card':10}
