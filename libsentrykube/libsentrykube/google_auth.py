import time

from typing import Any, Mapping
import google.auth.crypt
import google.auth.jwt
from google.oauth2.service_account import IDTokenCredentials


def get_signed_jwt(sa_info: Mapping[str, Any], jwt_ttl: int = 3600) -> bytes:
    sa_credentials = google.auth.jwt.Credentials.from_service_account_info(
        sa_info,
        # vault/{ROLE} where ROLE matches the "role" in the payload
        # we send to vault gcp login in vault_authenticate.
        audience="vault/iam",
    )
    now = int(time.time())
    email = sa_credentials.signer_email
    payload = {
        "iat": now,
        "exp": now + jwt_ttl,
        "iss": email,
        "sub": email,
        "email": email,
    }
    jwt = google.auth.jwt.encode(signer=sa_credentials.signer, payload=payload)
    return jwt


def derive_oidc_credentials(sa_info: Mapping[str, Any], **kwargs) -> IDTokenCredentials:
    return IDTokenCredentials.from_service_account_info(
        sa_info,
        **kwargs,
    )
