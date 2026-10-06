import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy import text

from config import settings
from database import engine

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

security = HTTPBearer()
password_hash = PasswordHash.recommended()


def create_access_token(
    data: dict,
    expires_delta: timedelta | None = None,
):
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({"exp": expire})

    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def verify_token(token: str):
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
        )

        return payload

    except InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
):
    token = credentials.credentials

    payload = verify_token(token)

    user_id = payload.get("sub")

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    with engine.connect() as connection:
        result = connection.execute(
            text("""
                SELECT id, username, email, role
                FROM users
                WHERE id = :user_id
            """),
            {"user_id": int(user_id)},
        )

        user = result.mappings().first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )
    return user


def require_admin(current_user=Depends(get_current_user)):
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return current_user


def create_refresh_token(user_id: int):
    raw_token = secrets.token_urlsafe(32)

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(hours=24)

    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO refresh_tokens (
                    user_id,
                    token_hash,
                    expires_at,
                    created_at
                )
                VALUES (
                    :user_id,
                    :token_hash,
                    :expires_at,
                    :created_at
                )
            """),
            {
                "user_id": user_id,
                "token_hash": token_hash,
                "expires_at": expires_at,
                "created_at": created_at,
            },
        )

    return raw_token
