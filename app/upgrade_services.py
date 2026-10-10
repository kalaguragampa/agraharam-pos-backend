"""Apply service metadata migration and the requested 5% inclusive billing rate."""
from decimal import Decimal
from .seed import seed
from .db import SessionLocal
from .models import CompanySettings

def main():
    seed()
    with SessionLocal() as db:
        settings=db.get(CompanySettings,1)
        settings.tax_rate=Decimal('5.00')
        db.commit()
    print('Service fields ready. New bills use 5% inclusive tax; historic bills unchanged.')
if __name__=='__main__':main()
