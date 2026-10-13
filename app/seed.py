"""Initialize a fresh database and provision the requested administrator once."""
import json
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, inspect, text
from .db import Base, engine, SessionLocal
from .models import User, CompanySettings, Category, MenuItem, Branch

def seed():
    Base.metadata.create_all(engine)
    additions={'customers':{'gstin':"VARCHAR(15) NOT NULL DEFAULT ''"},'company_settings':{'gstin':"VARCHAR(15) NOT NULL DEFAULT ''",'state_code':"VARCHAR(2) NOT NULL DEFAULT ''",'business_name':"VARCHAR(100) NOT NULL DEFAULT 'Agrahaaram Food Court'",'branch_name':"VARCHAR(100) NOT NULL DEFAULT 'LB Nagar'",'legal_name':"VARCHAR(100) NOT NULL DEFAULT 'KGSV Enterprises'",'fssai_number':"VARCHAR(20) NOT NULL DEFAULT '13625010000764'",'invoice_prefix':"VARCHAR(12) NOT NULL DEFAULT 'AGLB'"},'users':{'branch_id':'VARCHAR(10)','username':'VARCHAR(80)','passcode_ciphertext':'VARCHAR(255)'},'menu_items':{'service_details':"JSON NOT NULL DEFAULT '{}'"},'orders':{'branch_id':"VARCHAR(10) NOT NULL DEFAULT 'BR002'",'payment_details':"JSON NOT NULL DEFAULT '[]'",'staff_username':"VARCHAR(80) NOT NULL DEFAULT ''",'gst_details':"JSON NOT NULL DEFAULT '{}'",'bill_number':'VARCHAR(50)','receipt_details':"JSON NOT NULL DEFAULT '{}'",'status':"VARCHAR(20) NOT NULL DEFAULT 'Completed'",'cancellation':"JSON NOT NULL DEFAULT '{}'"}}
    for table,columns in additions.items():
        existing={c['name'] for c in inspect(engine).get_columns(table)}
        with engine.begin() as connection:
            for name,ddl in columns.items():
                if name not in existing:connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
    # Backfill all existing orders to LB Nagar without modifying their bills.
    # Existing users remain unassigned until branch permissions are configured.
    with engine.begin() as connection:
        connection.execute(text("UPDATE orders SET branch_id = 'BR002' WHERE branch_id IS NULL"))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_orders_branch_id ON orders (branch_id)'))
        connection.execute(text('CREATE INDEX IF NOT EXISTS ix_users_branch_id ON users (branch_id)'))
        connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_bill_number ON orders (bill_number)'))
    provision=json.loads((Path(__file__).parent/'bootstrap_admin.json').read_text())
    with SessionLocal() as db:
        for branch_id,name,prefix in [('BR001','Aziz Nagar','AGAN'),('BR002','LB Nagar','AGLB')]:
            if not db.get(Branch,branch_id):
                db.add(Branch(id=branch_id,name=name,invoice_prefix=prefix,gstin='36AGPPM0820M1Z4',active=True))
        db.flush()
        if not db.scalar(select(User).where(User.email==provision['email'])):
            db.add(User(id=str(uuid4()),email=provision['email'],name='Agraharam Administrator',password_hash=provision['password_hash'],is_admin=True,active=True,permissions=[]))
        if not db.get(CompanySettings,1):db.add(CompanySettings(id=1,address='',phone='',tax_rate=5))
        for name in db.scalars(select(MenuItem.category).distinct()):
            if not db.scalar(select(Category).where(Category.name==name)):
                db.add(Category(id=str(uuid4()),name=name,active=True))
        db.flush()
        # Preserve existing cashier billing access at LB Nagar until reassigned by an admin.
        for existing_user in db.scalars(select(User).where(User.is_admin.is_(False),User.branch_id.is_(None))):
            existing_user.branch_id='BR002'
        db.flush()
        used={u.username for u in db.scalars(select(User)) if u.username}
        import re
        for u in db.scalars(select(User)):
            if not u.username:
                base=re.sub(r'[^a-z0-9_.-]','',u.email.split('@')[0].lower())[:60] or 'staff'
                u.username=base if base not in used else base+'-'+u.id[:8]
                used.add(u.username)
        db.flush()
        db.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username)'))
        db.commit()
    print('Database initialized. Administrator provisioned if absent; existing users unchanged.')
if __name__=='__main__':seed()
