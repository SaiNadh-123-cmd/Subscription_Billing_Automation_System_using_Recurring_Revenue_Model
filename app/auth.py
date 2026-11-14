"""
Authentication and JWT Utilities.
Uses pure-Python passlib (pbkdf2_sha256) and PyJWT to eliminate C-extension dependencies.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.hash import pbkdf2_sha256
from sqlalchemy.orm import Session

from app.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from app.database import get_db
from app.models import Customer

# OAuth2 scheme configured for Swagger UI authentication
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/auth/signin",
    auto_error=False,
)


def hash_password(password: str) -> str:
    """Hashes a plaintext password using pbkdf2_sha256 (pure Python)."""
    return pbkdf2_sha256.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plaintext password against a stored pbkdf2_sha256 hash."""
    return pbkdf2_sha256.verify(plain_password, hashed_password)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Creates a signed JWT access token.
    Claims: sub (subject/email), id, role, exp (expiration time).
    """
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire, "iat": now})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decodes and validates a JWT access token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Customer:
    """
    FastAPI dependency that extracts and validates the bearer token,
    returning the authenticated Customer entity from PostgreSQL.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials. Please sign in.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    payload = decode_access_token(token)
    email: Optional[str] = payload.get("sub")
    if email is None:
        raise credentials_exception

    user = db.query(Customer).filter(Customer.email == email).first()
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    return user


def get_optional_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[Customer]:
    """
    FastAPI dependency that extracts and validates the bearer token if provided.
    Returns Customer entity if authenticated, or None if no token or token is invalid.
    """
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        email: Optional[str] = payload.get("sub")
        if email:
            user = db.query(Customer).filter(Customer.email == email).first()
            if user and user.is_active:
                return user
    except Exception:
        return None
    return None

