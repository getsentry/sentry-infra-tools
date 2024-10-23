import click
from typing import Any, Mapping, cast
from pathlib import Path

from libsentrykube import httpx_client
from libsentrykube.google_auth import get_signed_jwt

VAULT_URI = "https://vault.getsentry.net"


def authenticate(sa_info: Mapping[str, Any]) -> str:
    click.echo(f"Authenticating against vault: {VAULT_URI}")

    jwt = get_signed_jwt(sa_info)
    payload = {"role": "iam", "jwt": jwt.decode("utf-8")}

    res = httpx_client.post(f"{VAULT_URI}/v1/auth/gcp/login", json=payload)
    if res.status_code != 200:
        raise Exception(
            f"Got non-200 response code: {res.status_code}.\nError: {res.text}"
        )

    return cast(str, res.json()["auth"]["client_token"])


def sign_pubkey(vault_token: str, id_key_path: Path) -> Path:
    click.echo(f"Getting vault to sign `{id_key_path}`.")

    public_key = id_key_path
    private_key = public_key.with_suffix("")  # strips the suffix
    payload = {"public_key": public_key.read_text()}

    res = httpx_client.post(
        f"{VAULT_URI}/v1/ssh-client-signer/sign/sign",
        headers={"X-Vault-Token": vault_token},
        json=payload,
    )
    if res.status_code != 200:
        raise Exception(
            f"Got non-200 response code: {res.status_code}.\nError: {res.text}"
        )

    signed_key_data = res.json()["data"]["signed_key"]
    private_cert = private_key.with_suffix(".cert")
    public_cert = private_key.with_suffix(".cert.pub")
    public_cert.write_text(signed_key_data)

    # make ssh happy
    public_cert.chmod(0o600)

    try:
        # missing_ok=True was added in Python 3.8.
        # Let's not force that minimum version on everyone right now.
        private_cert.unlink()
    except FileNotFoundError:
        pass

    private_cert.symlink_to(private_key)
    return private_cert
