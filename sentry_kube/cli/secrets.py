import hmac
import json
from base64 import standard_b64decode, standard_b64encode
from hashlib import pbkdf2_hmac, sha256
import os
from secrets import token_urlsafe

import click
from google.api_core import exceptions
from google.cloud import secretmanager
from kubernetes.client import CoreV1Api, V1Secret
from kubernetes.client.rest import ApiException

from libsentrykube.utils import kube_get_client

__all__ = ("secrets",)

# run this script only once per region.
# this script generates and uploads the necessary username and passwords for postgres pgbouncer users
# but also could be useful for other operations with secrets.

salt_size = 16
digest_len = 32
iterations = 4096


cmd_help = """

This command simplifies creation of users and corresponding userlist entries in the following
places:

- plaintext passwords in k8s secret `sentry-db-password`, to be used by the `getsentry` deployment,
  `sentry` container;

- userlist in k8s secret `service-pgbouncer`, to be used by intermediary and sidecar `pgbouncer`
  containers;

- userlist in GCP Secret Manager secret `postgres`, to be used by Salt on `db-*` instances.

The script is addition-only. This is a safety net to avoid accidental user removal.


## Step 1

The first step generates plaintext passwords. You can specify several `--key` parameters if you
want:

\b
```
sentry-kube -C {env} secrets --generate-plaintext --key alice --key bob
```

The resulting password will be saved to `sentry-db-password` k8s secret (or you can override it with
the `--plaintext-k8s-secret` option) as a separate secret entry:

\b
```
apiVersion: v1
data:
  alice: YWFh
  bob: YmJi
kind: Secret
```

## Step 2

After plaintext secrets populated, it is possible to generate userlist entries out of them. There is
no need to specify usernames, as they will be taken from the plaintext k8s secret.

\b
```
sentry-kube -C {env} secrets --generate-userlist
```

This will update userlists both in k8s secrets and Secret Manager.


## Maintenance

> [!WARNING]
> Proceed with caution. This may start an incident if used carelessly.

If you really want to delete users, you can do so with standard commands:

\b
```
sentry-kube -q -C s4s kubectl edit secrets sentry-db-password
sentry-kube -q -C s4s edit-secret sentry-db-password
sentry-kube -q -C s4s edit-secret service-pgbouncer
```

"""


def b64enc(b: bytes) -> str:
    return standard_b64encode(b).decode("utf-8")


def pg_scram_sha256(passwd: str) -> str:
    salt = os.urandom(salt_size)
    digest_key = pbkdf2_hmac(
        "sha256", passwd.encode("utf8"), salt, iterations, digest_len
    )
    client_key = hmac.digest(digest_key, "Client Key".encode("UTF-8"), "sha256")
    stored_key = sha256(client_key).digest()
    server_key = hmac.digest(digest_key, "Server Key".encode("UTF-8"), "sha256")
    return (
        f"SCRAM-SHA-256${iterations}:{b64enc(salt)}"
        f"${b64enc(stored_key)}:{b64enc(server_key)}"
    )


def decode_userlist(userlist: str):
    # userlist is a base64 encoded list of users and their passwords.
    # the format is as follows:
    # "user1" "SCRAM-SHA-256...."
    # "user2" "SCRAM-SHA-256...."
    if userlist == "":
        return {}

    hashes = {}
    for line in userlist.rstrip("\n").split("\n"):
        (username, password_string) = line.split(" ")
        username = username.replace('"', "")
        password_string = password_string.replace('"', "")
        hashes[username] = password_string
    return hashes


def upload_plaintext_to_k8s_secret(
    api: CoreV1Api, users: dict[str, dict[str, str]], namespace: str, secret_name: str
) -> None:
    try:
        secret = api.read_namespaced_secret(namespace=namespace, name=secret_name)
    except ApiException as exc:
        if exc.status == 404:
            print(f"Secret `{namespace}/{secret_name}` does not exist, creating...")
            body = V1Secret()
            body.metadata = {"name": secret_name}
            body.data = {}
            secret = api.create_namespaced_secret(namespace=namespace, body=body)
        else:
            raise

    secret_data = secret.data if secret.data else {}

    print(f"### Kubernetes secret `{namespace}/{secret_name}`, BEFORE")
    for secret_item in secret_data:
        print(f"{secret_item}: {secret_data[secret_item]}")
    print()

    is_modified = False
    for user in users:
        if user not in secret_data:
            secret_data[user] = b64enc(users[user]["password"].encode("utf-8"))
            is_modified = True

    if not is_modified:
        print(
            f"Kubernetes secret `{namespace}/{secret_name}` is up to date. No new users."
        )
        return
    print(f"### Kubernetes secret `{namespace}/{secret_name}`, AFTER")
    for secret_item in secret_data:
        print(f"{secret_item}: {secret_data[secret_item]}")
    print()

    confirm = input("Type 'yes' to apply: ")
    if confirm != "yes":
        print("WARNING: This change was not applied.")
        return

    api.patch_namespaced_secret(
        namespace=namespace, name=secret_name, body={"data": secret_data}
    )
    print("Updated successfully.")


def upload_plaintext_to_google_secret(
    project_id: str, users: dict[str, str], secret_id: str
):
    # Create the Secret Manager client.
    client = secretmanager.SecretManagerServiceClient()

    data = {}

    # get the current secret to ensure we're not double adding
    version_id = "latest"
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    try:
        response = client.access_secret_version(request={"name": name})
        payload = response.payload.data.decode("utf-8")
        data = json.loads(payload)
    except exceptions.NotFound:
        print(
            f"ERROR: The secret `{secret_id}` should be created with Terragrunt before running this script."
        )
        return

    merged_data = merge_secrets(data, users, f"Secret Manager secret `{secret_id}`")

    if merged_data:
        data = merged_data
        secret_data = json.dumps(data).encode("utf-8")
        parent = client.secret_path(project_id, secret_id)
        client.add_secret_version(
            request={"parent": parent, "payload": {"data": secret_data}}
        )
        print("Updated successfully.")


def merge_secrets(data: dict[str, str], users: dict[str, str], label: str):
    print(f"### {label}, BEFORE")
    for k in data:
        print(k, data[k])
    print()

    is_modified = False
    for user in users:
        if user not in data:
            data[user] = users[user]
            is_modified = True

    if not is_modified:
        print(f"{label} is up to date. No new users.\n")
        return

    print(f"### {label}, AFTER")
    for k in data:
        print(k, data[k])
    print()

    confirm = input("Type 'yes' to apply: ")
    if confirm != "yes":
        print("WARNING: This change was not applied.")
        return

    return data


def merge_userlists(
    encoded_userlist: str, users: dict[str, dict[str, str]], label: str
):
    userlist = standard_b64decode(encoded_userlist).decode("utf-8")

    print(f"### {label}, BEFORE")
    print(userlist)
    print()

    hashes = decode_userlist(userlist)

    is_modified = False
    for user in users:
        if user not in hashes:
            hashes[user] = users[user]["scram"]
            is_modified = True

    if not is_modified:
        print(f"{label} is up to date. No new users.\n")
        return

    userlist = ""
    for entry in hashes:
        userlist += f'"{entry}" "{hashes[entry]}"\n'

    print(f"### {label}, AFTER")
    print(userlist)
    print()

    confirm = input("Type 'yes' to apply: ")
    if confirm != "yes":
        print("WARNING: This change was not applied.")
        return

    encoded_userlist = standard_b64encode(userlist.encode("utf-8")).decode("utf-8")
    return encoded_userlist


def upload_userlist_to_k8s_secret(
    api: CoreV1Api, users: dict[str, dict[str, str]], secret_name: str
) -> None:
    try:
        secret = api.read_namespaced_secret(namespace="default", name=secret_name)
    except ApiException as exc:
        if exc.status == 404:
            print(f"Secret `default/{secret_name}` does not exist, creating...")
            body = V1Secret()
            body.metadata = {"name": secret_name}
            body.data = {"userlist": ""}
            api.create_namespaced_secret(namespace="default", body=body)
            secret = api.read_namespaced_secret(namespace="default", name=secret_name)
        else:
            raise

    secret = api.read_namespaced_secret(namespace="default", name=secret_name)
    encoded_userlist = secret.data["userlist"]
    merged_userlist = merge_userlists(
        encoded_userlist, users, f"Kubernetes secret `default/{secret_name}`"
    )
    if merged_userlist:
        api.patch_namespaced_secret(
            namespace="default",
            name=secret_name,
            body={"data": {"userlist": merged_userlist}},
        )
        print("Updated successfully.")


def upload_userlist_to_google_secret(
    project_id: str, users: dict[str, dict[str, str]], secret_id: str
) -> None:
    # run gcloud auth application-default login for the gcloud python lib

    # Create the Secret Manager client.
    client = secretmanager.SecretManagerServiceClient()

    data = {"userlist": ""}

    # get the current secret to ensure we're not double adding
    version_id = "latest"
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    try:
        response = client.access_secret_version(request={"name": name})
        payload = response.payload.data.decode("utf-8")
        data = json.loads(payload)
    except exceptions.NotFound:
        print(
            f"ERROR: The secret `{secret_id}` should be created with Terragrunt before running this script."
        )
        return

    # decode the userlist
    encoded_userlist = data["userlist"]
    merged_userlist = merge_userlists(
        encoded_userlist, users, f"Secret Manager secret `{secret_id}`"
    )
    if merged_userlist:
        data["userlist"] = merged_userlist
        secret_data = json.dumps(data).encode("utf-8")
        parent = client.secret_path(project_id, secret_id)
        client.add_secret_version(
            request={"parent": parent, "payload": {"data": secret_data}}
        )
        print("Updated successfully.")


@click.command()
@click.option("--key", "key_tuple", required=False, multiple=True, type=str)
@click.option("--generate-plaintext", type=bool, default=False, is_flag=True)
@click.option("--generate-userlist", type=bool, default=False, is_flag=True)
@click.option("--copy-entry", type=bool, default=False, is_flag=True)
@click.option("--plaintext-k8s-secret", default="sentry-db-password", type=str)
@click.option("--userlist-k8s-secret", default="service-pgbouncer", type=str)
@click.option("--plaintext-sm-secret-id", default="kafka", type=str)
@click.option("--userlist-sm-secret-id", default="postgres", type=str)
@click.option("--value", default=None, type=str)
@click.option("--sm-key", default=None, type=str)
@click.pass_context
def secrets(
    ctx,
    key_tuple,
    generate_plaintext,
    generate_userlist,
    copy_entry,
    plaintext_k8s_secret,
    userlist_k8s_secret,
    plaintext_sm_secret_id,
    userlist_sm_secret_id,
    value,
    sm_key,
):
    project_id = ctx.obj.cluster.services_data["project"]
    client = kube_get_client()
    api = CoreV1Api(client)

    if not (generate_plaintext or generate_userlist or copy_entry):
        print(
            "Either --generate-plaintext, --generate-userlist, or --copy-entry should be specified."
        )
        return

    if generate_userlist and key_tuple:
        print("You should not specify --key when using --generate-userlist")
        return

    # fetch current list of users
    users = {}
    secret_data = {}
    namespace = "default"
    try:
        if "/" in plaintext_k8s_secret:
            namespace, plaintext_k8s_secret = plaintext_k8s_secret.split("/")
        secret = api.read_namespaced_secret(
            namespace=namespace, name=plaintext_k8s_secret
        )
        secret_data = secret.data
    except ApiException as exc:
        if exc.status != 404:
            raise

    if secret_data:
        for secret_item in secret_data:
            password = standard_b64decode(secret_data[secret_item]).decode("utf-8")
            users[secret_item] = {
                "password": password,
                "scram": pg_scram_sha256(password),
            }

    # Step 1: generate plaintext secrets
    if generate_plaintext:
        for user in key_tuple:
            new_value = value if value else token_urlsafe(16)
            users[user] = {
                "password": new_value,
                "scram": pg_scram_sha256(new_value),
            }

        upload_plaintext_to_k8s_secret(api, users, namespace, plaintext_k8s_secret)

    # Step 2: generate userlists from plaintext secrets
    if generate_userlist:
        # removing plaintext passwords just to be more secure :muscle:
        users = {k: {"scram": users[k]["scram"]} for k in users}

        upload_userlist_to_k8s_secret(api, users, userlist_k8s_secret)
        upload_userlist_to_google_secret(project_id, users, userlist_sm_secret_id)

    # Other mode: copying entries from K8s secrets to Secret Manager
    if copy_entry:
        if sm_key and len(key_tuple) > 1:
            print(
                "The `--sm-key` argument should not be used when specifying multiple `--username` values."
            )
            return

        users = {}
        for user in key_tuple:
            _key = sm_key if sm_key else user
            users[_key] = secret_data[user]

        upload_plaintext_to_google_secret(project_id, users, plaintext_sm_secret_id)
