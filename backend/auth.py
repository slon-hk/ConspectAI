"""Compatibility wrapper for authentication and security helpers."""

from app.core.security import (
    create_access_token,
    decode_token,
    get_current_user,
    hash_password,
    hash_token,
    make_verification_token,
    oauth2,
    send_verification_email,
    verify_captcha,
    verify_password,
)

__all__ = [
    "create_access_token",
    "decode_token",
    "get_current_user",
    "hash_password",
    "hash_token",
    "make_verification_token",
    "oauth2",
    "send_verification_email",
    "verify_captcha",
    "verify_password",
]
