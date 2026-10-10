"""Admin-only passcode retrieval; login uses Argon2 hashes separately."""
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from .config import config

def cipher():
    key=config.passcode_encryption_key
    if not key:
        if config.app_env=='production':raise HTTPException(503,'Configure PASSCODE_ENCRYPTION_KEY before managing passcodes')
        path=Path('.passcode_encryption_key')
        try:
            with path.open('xb') as f:
                path.chmod(0o600);f.write(Fernet.generate_key())
        except FileExistsError:pass
        key=path.read_text().strip()
    try:return Fernet(key.encode())
    except ValueError:raise HTTPException(503,'Invalid passcode encryption key')
def encrypt(value):return cipher().encrypt(value.encode()).decode()
def decrypt(value):
    try:return cipher().decrypt(value.encode()).decode()
    except InvalidToken:raise HTTPException(503,'Passcode key differs from the key used to save this credential')
