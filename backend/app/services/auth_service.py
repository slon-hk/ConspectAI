"""Authentication orchestration service."""

from __future__ import annotations

import auth
from app.repositories.oltp import UserRepository
from app.services.user_service import UserService


class AuthServiceError(Exception):
    pass


class UsernameTooShortError(AuthServiceError):
    pass


class PasswordTooShortError(AuthServiceError):
    pass


class AgreementRequiredError(AuthServiceError):
    pass


class EmailAlreadyExistsError(AuthServiceError):
    pass


class UsernameAlreadyExistsError(AuthServiceError):
    pass


class InvalidCredentialsError(AuthServiceError):
    pass


class AuthAccountBlockedError(AuthServiceError):
    pass


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
        user_service: UserService,
        default_plan_key: str,
    ) -> None:
        self._user_repository = user_repository
        self._user_service = user_service
        self._default_plan_key = default_plan_key

    async def register(
        self,
        *,
        username: str,
        email: str,
        password: str,
        agreement: bool,
    ) -> dict:
        normalized_username = username.strip()
        normalized_email = email.strip().lower()

        if len(normalized_username) < 2:
            raise UsernameTooShortError("Имя пользователя слишком короткое (мин. 2 символа)")
        if len(password) < 6:
            raise PasswordTooShortError("Пароль слишком короткий (мин. 6 символов)")
        if not agreement:
            raise AgreementRequiredError(
                "Для регистрации необходимо принять условия оферты и политики конфиденциальности"
            )

        if await self._user_repository.get_by_email(normalized_email):
            raise EmailAlreadyExistsError("Пользователь с таким email уже существует")
        if await self._user_repository.get_by_username(normalized_username):
            raise UsernameAlreadyExistsError("Это имя пользователя уже занято")

        password_hash = auth.hash_password(password)
        user = await self._user_repository.create(
            normalized_username,
            normalized_email,
            password_hash,
            self._default_plan_key,
        )
        return await self._auth_payload(user)

    async def login(self, *, email: str, password: str) -> dict:
        normalized_email = email.strip().lower()
        user = await self._user_repository.get_by_email(normalized_email)

        if not user or not auth.verify_password(password, user["password_hash"]):
            raise InvalidCredentialsError("Неверный email или пароль")
        if user.get("is_blocked"):
            raise AuthAccountBlockedError("Аккаунт заблокирован администратором")

        return await self._auth_payload(user)

    async def _auth_payload(self, user: dict) -> dict:
        return {
            "access_token": auth.create_access_token(user["id"]),
            "token_type": "bearer",
            "user": await self._user_service.to_safe_user(user),
            "raw_user": user,
        }
