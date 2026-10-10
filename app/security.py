import hashlib, secrets
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pwdlib import PasswordHash
from .db import get_db
from .models import User, LoginSession
from .schemas import PERMISSIONS
hasher = PasswordHash.recommended()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(24))
def token_hash(token): return hashlib.sha256(token.encode()).hexdigest()
def current_user(request: Request, db: Session = Depends(get_db)):
    token=request.cookies.get('agraharam_session')
    if not token: raise HTTPException(401,'Please sign in')
    session=db.get(LoginSession,token_hash(token))
    if not session: raise HTTPException(401,'Session expired')
    expiry=session.expires_at
    if expiry.tzinfo is None: expiry=expiry.replace(tzinfo=timezone.utc)
    if expiry<=datetime.now(timezone.utc): raise HTTPException(401,'Session expired')
    user=db.get(User,session.user_id)
    if not user or not user.active: raise HTTPException(401,'Account is inactive')
    return user

def require(*permissions):
    def check(user: User = Depends(current_user)):
        if not user.is_admin and not any(p in user.permissions for p in permissions):
            raise HTTPException(403,'You do not have access to this menu')
        return user
    return check

def admin(user: User = Depends(current_user)):
    if not user.is_admin: raise HTTPException(403,'Administrator access required')
    return user

def user_view(user):
    return {'id':user.id,'username':user.username or user.email.split('@')[0],'has_saved_passcode':bool(user.passcode_ciphertext),'email':user.email,'name':user.name,'active':user.active,'is_admin':user.is_admin,'permissions':PERMISSIONS if user.is_admin else user.permissions}
