from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
PERMISSIONS = ['dashboard','billing','orders','cancel_bills','menu','customers','reports','report_pos','report_services','report_gst','settings']
class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    @field_validator('*', mode='before')
    @classmethod
    def trim_text(cls, value, info):
        return value.strip() if isinstance(value,str) and info.field_name != 'password' else value
class LoginIn(BaseModel):
    username: str | None = Field(default=None,min_length=1,max_length=254)
    passcode: str | None = Field(default=None,min_length=1,max_length=128)
    email: EmailStr | None = None
    password: str | None = Field(default=None,min_length=1,max_length=128)
    @model_validator(mode='after')
    def credentials(self):
        if not (self.username or self.email) or not (self.passcode or self.password): raise ValueError('Username and passcode required')
        return self
class UserIn(Payload):
    username: str | None = Field(default=None,min_length=1,max_length=80,pattern=r'^[a-z0-9_.-]+$')
    email: EmailStr
    name: str = Field(min_length=1,max_length=80)
    password: str | None = Field(default=None,min_length=8,max_length=128)
    passcode: str | None = Field(default=None,pattern=r'^[0-9]{4}$')
    is_admin: bool = False
    active: bool = True
    permissions: list[str] = Field(default_factory=list)
    @field_validator('permissions')
    @classmethod
    def valid_permissions(cls,value):
        if any(p not in PERMISSIONS for p in value): raise ValueError('Unknown menu permission')
        return list(dict.fromkeys(value))
    @model_validator(mode='after')
    def manual_credential(self):
        if not self.passcode and not self.password:raise ValueError('Enter a four-digit passcode; automatic generation is disabled')
        return self
class UserEdit(Payload):
    email: EmailStr | None = None
    username: str | None = Field(default=None,min_length=1,max_length=80,pattern=r'^[a-z0-9_.-]+$')
    name: str = Field(min_length=1,max_length=80)
    is_admin: bool = False
    active: bool = True
    permissions: list[str] = Field(default_factory=list)
    password: str | None = Field(default=None,min_length=8,max_length=128)
    passcode: str | None = Field(default=None,pattern=r'^[0-9]{4}$')
    valid_permissions = field_validator('permissions')(UserIn.valid_permissions.__func__)
class MenuIn(Payload):
    image: str = Field(default='',max_length=750000)
    @field_validator('image')
    @classmethod
    def image_valid(cls,value):
        if not value:return value
        import base64, io
        from PIL import Image
        try:
            header,data=value.split(',',1)
            if header not in ('data:image/png;base64','data:image/jpeg;base64','data:image/webp;base64'):raise ValueError()
            raw=base64.b64decode(data,validate=True)
            if len(raw)>550000:raise ValueError()
            with Image.open(io.BytesIO(raw)) as im:
                if im.format not in ('PNG','JPEG','WEBP') or im.width>2048 or im.height>2048:raise ValueError()
                im.verify()
        except Exception:raise ValueError('Upload a valid PNG, JPEG or WebP image under 500 KB and 2048 pixels')
        return value

    code: str = Field(default='',max_length=40)
    duration_minutes: int = Field(default=0,ge=0,le=1440)
    sac_code: str = Field(default='',max_length=12,pattern=r'^\d*$')
    hsn_code: str = Field(default='',max_length=12,pattern=r'^\d*$')
    gst_rate_override: Decimal | None = Field(default=None,ge=0,le=30,max_digits=5,decimal_places=2)
    description: str = Field(default='',max_length=1000)
    name: str = Field(min_length=1,max_length=80)
    category: str = Field(min_length=1,max_length=40)
    price: Decimal = Field(gt=0,le=100000,max_digits=12,decimal_places=2)
    active: bool = True
class CustomerIn(Payload):
    gstin: str = Field(default='',pattern=r'^$|^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$')

    name: str = Field(min_length=1,max_length=80)
    phone: str = Field(default='',max_length=20)
class SettingsIn(Payload):
    business_name: str = Field(default='Agrahaaram Food Court',max_length=100)
    branch_name: str = Field(default='LB Nagar',max_length=100)
    legal_name: str = Field(default='KGSV Enterprises',max_length=100)
    fssai_number: str = Field(default='13625010000764',max_length=20,pattern=r'^$|^[0-9]{14}$')
    invoice_prefix: str = Field(default='AGLB',min_length=1,max_length=12,pattern=r'^[A-Z0-9]+$')
    gstin: str = Field(default='',pattern=r'^$|^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$')
    state_code: str = Field(default='',pattern=r'^$|^[0-9]{2}$')
    @model_validator(mode='after')
    def seller_state(self):
        if self.gstin and self.state_code and self.gstin[:2]!=self.state_code:raise ValueError('GSTIN and business state must match')
        return self
    address: str = Field(default='',max_length=200)
    phone: str = Field(default='',max_length=20)
    tax_rate: Decimal = Field(default=0,ge=0,le=30,max_digits=5,decimal_places=2)
class LineIn(Payload):
    item_id: UUID
    qty: int = Field(ge=1,le=1000)
class PaymentIn(Payload):
    method: Literal['Cash','UPI','Card']
    amount: Decimal = Field(gt=0,le=10000000000,max_digits=14,decimal_places=2)
class OrderIn(Payload):
    cash_received: Decimal | None = Field(default=None,gt=0,le=10000000000,max_digits=14,decimal_places=2)
    branch_id: Literal['BR001','BR002'] | None = None
    payments: list[PaymentIn] | None = Field(default=None,min_length=1,max_length=3)

    place_of_supply: str = Field(default='',pattern=r'^$|^[0-9]{2}$')
    buyer_gstin: str = Field(default='',pattern=r'^$|^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$')
    request_id: UUID
    expected_total: Decimal = Field(gt=0,le=10000000000,max_digits=14,decimal_places=2)
    customer_id: UUID | None = None
    order_type: Literal['Item','Service','Dine-in','Takeaway']
    payment: Literal['Cash','UPI','Card','Split']
    items: list[LineIn] = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def validate_payments(self):
        cash_due=self.expected_total if self.payment=='Cash' else (next((p.amount for p in (self.payments or []) if p.method=='Cash'),Decimal('0')) if self.payment=='Split' else Decimal('0'))
        if self.cash_received is not None and (cash_due<=0 or self.cash_received<cash_due):raise ValueError('Cash received must be at least the cash amount due')
        if self.payment=='Cash' and self.cash_received is None:raise ValueError('Enter cash received before completing the bill')
        if self.payment=='Split' and (not self.payments or len(self.payments)<2):raise ValueError('Split payment requires at least two payment methods')
        if self.payments:
            methods=[p.method for p in self.payments]
            if len(set(methods))!=len(methods):raise ValueError('Each payment method can appear only once')
            if sum((p.amount for p in self.payments),Decimal('0'))!=self.expected_total:raise ValueError('Payment amounts must equal the bill total')
            if self.payment!='Split' and (len(methods)!=1 or methods[0]!=self.payment):raise ValueError('Payment method does not match the payment breakdown')
        return self

class CategoryIn(Payload):
    name: str = Field(min_length=1,max_length=40)
    active: bool = True
class CancelIn(Payload):
    reason: str = Field(min_length=3,max_length=300)

from datetime import datetime
class OfflineOrderIn(Payload):
    order: OrderIn
    catalogue_token: str = Field(min_length=1,max_length=250000)
    created_at: datetime
