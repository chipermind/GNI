"""
Auth routes: token exchange (API key -> JWT).
"""
from fastapi import APIRouter, HTTPException, Security

from apps.api.auth import API_KEY, api_key_header, auth_required, create_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token")
def get_token(api_key: str = Security(api_key_header)):
    """
    Exchange API key for JWT. Requires X-API-Key header.
    Returns JWT for use as Bearer token. Requires JWT_SECRET and API_KEY to be set.
    """
    if not auth_required():
        return {"token": None, "message": "Auth not configured"}
    if not api_key or api_key.strip() != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        token = create_token()
        return {"token": token, "expires_in": 86400}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
