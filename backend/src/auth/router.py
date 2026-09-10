"""Authentication routes: signup, login, current user."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from src.auth.schemas import LoginRequest, Token, UserCreate, UserResponse
from src.auth.service import AuthError, authenticate, create_user
from src.core.dependencies import get_current_user
from src.core.ratelimit import rate_limit
from src.core.security import create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# Blunt credential stuffing and accidental hammering. Per-worker and in-memory —
# see src/core/ratelimit.py for what that does and doesn't buy you.
_signup_limit = rate_limit(max_calls=10, window_seconds=3600, scope="auth:signup")
_login_limit = rate_limit(max_calls=10, window_seconds=300, scope="auth:login")


def _token_for(user: dict) -> Token:
    access_token = create_access_token(
        {"sub": user["id"], "username": user["username"]}
    )
    return Token(token=access_token, user=UserResponse(**user))


@router.post("/signup", response_model=Token, dependencies=[Depends(_signup_limit)])
async def signup(payload: UserCreate):
    try:
        user = create_user(payload)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("User signed up: %s", user["username"])
    return _token_for(user)


@router.post("/login", response_model=Token, dependencies=[Depends(_login_limit)])
async def login(payload: LoginRequest):
    try:
        user = authenticate(payload.username, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("User logged in: %s", user["username"])
    return _token_for(user)


@router.get("/me", response_model=UserResponse)
async def me(current_user: dict = Depends(get_current_user)):
    from src.features.profile.service import get_profile

    profile = get_profile(current_user["user_id"])
    if not profile:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(
        id=profile["id"], username=profile["username"], email=profile["email"]
    )
