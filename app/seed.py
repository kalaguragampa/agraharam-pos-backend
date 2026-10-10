"""Initialize a fresh database and provision the requested administrator once."""
import json
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, inspect, text
from .db import Base, engine, SessionLocal
from .models import User, CompanySettings, Category, MenuItem

def seed():
    Base.metadata.create_all(engine)
    additions={'customers':{'gstin':"VARCHAR(15) NOT NULL DEFAULT ''"},'company_settings':{'gstin':"VARCHAR(15) NOT NULL DEFAULT ''",'state_code':"VARCHAR(2) NOT NULL DEFAULT ''"},'users':{'username':'VARCHAR(80)','passcode_ciphertext':'VARCHAR(255)'},'menu_items':{'service_details':"JSON NOT NULL DEFAULT '{}'"},'orders':{'payment_details':"JSON NOT NULL DEFAULT '[]'",'staff_username':"VARCHAR(80) NOT NULL DEFAULT ''",'gst_details':"JSON NOT NULL DEFAULT '{}'",'status':"VARCHAR(20) NOT NULL DEFAULT 'Completed'",'cancellation':"JSON NOT NULL DEFAULT '{}'"}}
    for table,columns in additions.items():
        existing={c['name'] for c in inspect(engine).get_columns(table)}
        with engine.begin() as connection:
            for name,ddl in columns.items():
                if name not in existing:connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
    provision=json.loads((Path(__file__).parent/'bootstrap_admin.json').read_text())
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.email==provision['email'])):
            db.add(User(id=str(uuid4()),email=provision['email'],name='Agraharam Administrator',password_hash=provision['password_hash'],is_admin=True,active=True,permissions=[]))
        if not db.get(CompanySettings,1):db.add(CompanySettings(id=1,address='',phone='',tax_rate=5))
        for name in db.scalars(select(MenuItem.category).distinct()):
            if not db.scalar(select(Category).where(Category.name==name)):
                db.add(Category(id=str(uuid4()),name=name,active=True))
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
