"""GST management summary from immutable bill tax snapshots."""
from decimal import Decimal, ROUND_HALF_UP

def tax_snapshot(gstin,state,buyer,pos,tax,total):
    state=state or (gstin[:2] if gstin else '')
    if not gstin or not state or not pos:return {'classification':'UNCLASSIFIED','reason':'Business GSTIN or place of supply missing'}
    interstate=pos!=state
    cgst=Decimal('0') if interstate else (tax/2).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    return {'seller_gstin':gstin,'seller_state':state,'buyer_gstin':buyer,'place_of_supply':pos,'classification':'B2B' if buyer else 'B2CL' if interstate and total>100000 else 'B2CS','cgst':str(cgst),'sgst':str(Decimal('0') if interstate else tax-cgst),'igst':str(tax if interstate else Decimal('0')),'cess':'0.00'}

def gst_report_data(orders):
    names=['B2B','B2CL','B2CS','CDNR','CDNUR','EXEMP','HSN(B2B)','HSN(B2C)','DOC','UNCLASSIFIED']
    fields=['subtotal','cgst','sgst','igst','cess','total']
    rows={n:{'type':n,**{k:Decimal('0') for k in fields},'note':'','details':[]} for n in names}
    for n in ['CDNR','CDNUR','EXEMP']:
        rows[n].update({k:None for k in fields});rows[n]['note']='Not recorded: credit/debit notes and exemption classification are not implemented'
    for n in ['DOC']:
        rows[n].update({k:None for k in fields})
    hsn={}
    for o in orders:
        doc={'bill':f'AGR-{o.id:06d}','date':o.created_at,'status':o.status,'total':o.total}
        rows['DOC']['details'].append(doc)
        if o.status!='Completed':continue
        g=o.gst_details or {};kind=g.get('classification','UNCLASSIFIED')
        if kind not in ['B2B','B2CL','B2CS']:kind='UNCLASSIFIED'
        values={'subtotal':o.subtotal,'total':o.total,**{k:Decimal(str(g.get(k,0))) for k in ['cgst','sgst','igst','cess']}}
        r=rows[kind]
        for k in fields:r[k]+=values[k]
        r['details'].append({**doc,'gstin':g.get('buyer_gstin',''),'place_of_supply':g.get('place_of_supply',''),**values,'gst':o.tax})
        if kind=='UNCLASSIFIED':continue
        group='HSN(B2B)' if kind=='B2B' else 'HSN(B2C)'
        for k in fields:rows[group][k]+=values[k]
        remaining={k:values[k] for k in fields}
        for index,line in enumerate(o.items):
            key=(group,line.get('sac_code') or 'Missing HSN/SAC')
            h=hsn.setdefault(key,{'code':key[1],'qty':0,**{k:Decimal('0') for k in fields}});h['qty']+=line['qty']
            for k in fields:
                value=remaining[k] if index==len(o.items)-1 else (values[k]*Decimal(str(line['line_total']))/o.total).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                h[k]+=value;remaining[k]-=value
    for (group,_),r in hsn.items():rows[group]['details'].append(r)
    unknown=rows['UNCLASSIFIED'];unknown['note']='Tax split and B2B/B2C classification require review; historical bills were not changed'
    for k in ['cgst','sgst','igst','cess']:unknown[k]=None
    issued=len(orders);cancelled=sum(o.status=='Cancelled' for o in orders)
    rows['DOC']['note']=f'{issued} issued; {cancelled} cancelled; {issued-cancelled} active'
    return {'sections':list(rows.values()),'documents':{'issued':issued,'cancelled':cancelled,'active':issued-cancelled},'unclassified_count':len(unknown['details'])}
