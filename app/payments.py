from decimal import Decimal
METHODS=['Cash','UPI','Card']
def payment_details(body):
    return [{'method':p.method,'amount':str(p.amount)} for p in body.payments] if body.payments else [{'method':body.payment,'amount':str(body.expected_total)}]
def allocations(order):
    details=order.payment_details or [{'method':order.payment,'amount':str(order.total)}]
    return {k:sum((Decimal(str(p['amount'])) for p in details if p['method']==k),Decimal('0')) for k in METHODS}
def line_allocations(order):
    # Largest remainders conserve both each line total and each payment-method total.
    remaining={k:int(v*100) for k,v in allocations(order).items()};left=sum(remaining.values());result=[]
    for line in order.items:
        cents=int(Decimal(str(line['line_total']))*100)
        share={k:(cents*remaining[k]//left) if left else 0 for k in METHODS}
        extra=cents-sum(share.values())
        ranked=sorted(METHODS,key=lambda k:(cents*remaining[k]%left) if left else 0,reverse=True)
        for k in ranked:
            if extra and share[k]<remaining[k]:share[k]+=1;extra-=1
        if extra:raise ValueError('Unable to reconcile stored payment details')
        result.append({k:Decimal(v)/100 for k,v in share.items()})
        remaining={k:remaining[k]-share[k] for k in METHODS};left-=cents
    return result
