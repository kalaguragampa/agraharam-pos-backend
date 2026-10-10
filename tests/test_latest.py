from test_pos import fresh,admin_client,bill,ORIGIN
from fastapi.testclient import TestClient
from app.main import app

def test_username_passcode_reveal_and_grants():
    a=admin_client()
    assert a.post('/auth/login',json={'username':'support','passcode':'87654321'},headers=ORIGIN).status_code==200
    u=a.post('/users',json={'username':'cashier1','email':'one@example.com','name':'One','passcode':'0042','permissions':['report_gst']},headers=ORIGIN).json()
    c=TestClient(app)
    assert c.post('/auth/login',json={'username':'cashier1','passcode':'0042'},headers=ORIGIN).status_code==200
    assert a.get('/users/'+u['id']+'/passcode').json()['passcode']=='0042'
    assert c.get('/users/'+u['id']+'/passcode').status_code==403
    assert '0042' not in a.get('/users').text and '0042' not in a.get('/audit').text
    assert c.get('/reports/gst').status_code==200 and c.get('/reports').status_code==403
    a.put('/users/'+u['id'],json={'name':'Updated','permissions':['report_gst']},headers=ORIGIN)
    assert a.get('/users/'+u['id']+'/passcode').json()['passcode']=='0042'
    assert a.post('/users',json={'username':'cashier1','email':'two@example.com','name':'Two','passcode':'1234'},headers=ORIGIN).status_code==409
    assert a.post('/users',json={'email':'bad@example.com','name':'Bad','passcode':'12345'},headers=ORIGIN).status_code==422

def test_gst_image_and_cancellation():
    import base64,io
    from PIL import Image
    a=admin_client();buf=io.BytesIO();Image.new('RGB',(8,8),'brown').save(buf,format='PNG')
    image='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
    i=a.post('/menu',json={'name':'Image Item','category':'Items','price':105,'image':image},headers=ORIGIN).json()
    assert i['image']==image and a.get('/menu').json()[0]['image']==image
    snap=a.get('/offline/catalogue').json();assert snap['images'][i['id']]==image and 'image' not in snap['items'][0]
    o=a.post('/orders',json=bill(i,expected_total=210),headers=ORIGIN).json();assert 'image' not in o['items'][0]
    a.put('/settings',json={'tax_rate':10},headers=ORIGIN)
    r=a.get('/reports/gst').json();assert r['items'][0]['rate']==5 and r['totals']=={'bills':1,'subtotal':200,'tax':10,'total':210}
    assert 'CGST' in a.get('/reports/gst/export').text
    a.post('/orders/'+str(o['id'])+'/cancel',json={'reason':'Test cancellation'},headers=ORIGIN)
    assert a.get('/reports/gst').json()['totals']['total']==0
    assert a.post('/menu',json={'name':'Invalid','category':'Items','price':5,'image':'data:image/png;base64,bm90YW5pbWFnZQ=='},headers=ORIGIN).status_code==422

def test_manual_creation_email_edit_and_conflict():
    a=admin_client()
    assert a.post('/users',json={'username':'manual','name':'Manual','email':'manual@example.com'},headers=ORIGIN).status_code==422
    u=a.post('/users',json={'username':'manual','name':'Manual','email':'manual@example.com','passcode':'0037'},headers=ORIGIN).json()
    assert u['initial_passcode']=='0037'
    r=a.put('/users/'+u['id'],json={'name':'Renamed','username':'newname','email':'updated@example.com','permissions':['billing']},headers=ORIGIN)
    assert r.status_code==200 and r.json()['email']=='updated@example.com'
    c=TestClient(app)
    assert c.post('/auth/login',json={'username':'newname','passcode':'0037'},headers=ORIGIN).status_code==200
    assert a.get('/users/'+u['id']+'/passcode').json()['passcode']=='0037'
    r=a.put('/users/'+u['id'],json={'name':'Renamed','email':'support@kalaguragampa.com','permissions':[]},headers=ORIGIN)
    assert r.status_code==409

def test_gst_sections_hsn_split_and_history():
    a=admin_client();seller='36ABCDE1234F1Z5';buyer='27ABCDE1234F1Z5'
    assert a.put('/settings',json={'gstin':seller,'state_code':'27','tax_rate':5},headers=ORIGIN).status_code==422
    assert a.put('/settings',json={'gstin':seller,'state_code':'36','tax_rate':5},headers=ORIGIN).status_code==200
    i=a.post('/menu',json={'name':'Test','category':'Items','sac_code':'999723','price':105},headers=ORIGIN).json()
    for extra in [{'place_of_supply':'36'},{'place_of_supply':'27','buyer_gstin':buyer}]:
        assert a.post('/orders',json=bill(i,expected_total=210,**extra),headers=ORIGIN).status_code==201
    r=a.get('/reports/gst').json();rows={x['type']:x for x in r['sections']}
    assert rows['B2CS']['subtotal']==200 and rows['B2CS']['cgst']==5 and rows['B2CS']['sgst']==5
    assert rows['B2B']['igst']==10 and rows['B2B']['cgst']==0
    assert rows['HSN(B2C)']['details'][0]['code']=='999723'
    assert rows['HSN(B2B)']['subtotal']==rows['B2B']['subtotal']
    assert rows['CDNR']['total'] is None and rows['DOC']['total'] is None
    assert r['documents']=={'issued':2,'cancelled':0,'active':2}
    a.put('/settings',json={'tax_rate':10},headers=ORIGIN)
    assert a.get('/reports/gst').json()['sections']==r['sections']

def test_gst_classification_boundaries_and_rounding():
    from decimal import Decimal
    from app.gst import tax_snapshot
    def snap(total,tax='0.05',pos='27',buyer=''):
        return tax_snapshot('36ABCDE1234F1Z5','36',buyer,pos,Decimal(tax),Decimal(total))
    assert snap('100000')['classification']=='B2CS'
    assert snap('100001')['classification']=='B2CL'
    local=snap('100',pos='36')
    assert Decimal(local['cgst'])+Decimal(local['sgst'])==Decimal('.05')
    assert tax_snapshot('','36','','36',Decimal('5'),Decimal('105'))['classification']=='UNCLASSIFIED'
