import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pwdlib import PasswordHash
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from database import engine
from security import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    require_admin,
    security,
    verify_token,
)

router = APIRouter()

limiter = Limiter(key_func=get_remote_address)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserLoginRequest(BaseModel):
    username: str
    password: str


password_hash = PasswordHash.recommended()


@router.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
def register(user: UserCreate):
    hashed_password = password_hash.hash(user.password)
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    INSERT INTO users (username, email, hashed_password)
                    VALUES (:username, :email, :hashed_password)
                    RETURNING id, username, email
                """),
                {
                    "username": user.username,
                    "email": user.email,
                    "hashed_password": hashed_password,
                },
            )

            new_user = result.mappings().first()
            return new_user

    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User {user.username} already exists",
        )


@router.post("/api/auth/login", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
def login(user: UserLoginRequest, request: Request):
    with engine.connect() as connection:
        result = connection.execute(
            text(
                """SELECT id, username, email, hashed_password
                    FROM users
                    WHERE username = :username
                    """
            ),
            {"username": user.username},
        )

        user_detail = result.mappings().first()

        if user_detail is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        auth = password_hash.verify(user.password, user_detail["hashed_password"])

        if not auth:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

        access_token = create_access_token({"sub": str(user_detail["id"])})
        refresh_token = create_refresh_token(user_detail["id"])

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.get("/api/test-auth")
def test_auth(credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]):
    token = credentials.credentials

    payload = verify_token(token)

    return payload


@router.get("/api/users/me")
def me(current_user=Depends(get_current_user)):
    return current_user


@router.get("/api/admin/test")
def admin_test(current_user=Depends(require_admin)):
    return {
        "message": "Admin access granted",
        "user": current_user,
    }


@router.post("/api/auth/refresh")
def refresh_token(refresh_request: RefreshTokenRequest):
    token_hash = hashlib.sha256(refresh_request.refresh_token.encode()).hexdigest()

    with engine.begin() as connection:
        result = connection.execute(
            text("""
                SELECT id, user_id, expires_at, revoked_at
                FROM refresh_tokens
                WHERE token_hash = :token_hash
            """),
            {"token_hash": token_hash},
        )

        row = result.mappings().first()

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        if row["revoked_at"] is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has been revoked",
            )

        if row["expires_at"] <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has expired",
            )

        connection.execute(
            text("""
                UPDATE refresh_tokens
                SET revoked_at = NOW()
                WHERE id = :token_id
            """),
            {"token_id": row["id"]},
        )

    new_access_token = create_access_token({"sub": str(row["user_id"])})

    new_refresh_token = create_refresh_token(row["user_id"])

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


@router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(logout_request: RefreshTokenRequest):
    token_hash = hashlib.sha256(logout_request.refresh_token.encode()).hexdigest()

    with engine.begin() as connection:
        connection.execute(
            text("""
                UPDATE refresh_tokens
                SET revoked_at = NOW()
                WHERE token_hash = :token_hash
                  AND revoked_at IS NULL
            """),
            {"token_hash": token_hash},
        )
