"""Prepare private local credentials and ONE owner-run SQL file; execute no SQL."""

import argparse
import csv
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from revenue_platform.runtime import ROOT


def private_directory(directory):
    directory = Path(os.path.abspath(directory)).resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError("Credential directory must be outside the repository")
    return directory


def refresh_owner_sql(directory):
    """Refresh only SQL from existing encrypted keys; never rotate keys or edit config."""
    directory = private_directory(directory)
    settings = json.loads((directory / "runtime.json").read_text(encoding="utf-8"))
    sql = (ROOT / "snowflake/bootstrap.sql").read_text(encoding="utf-8")
    public_keys = []
    for prefix, user, token in [
        ("SNOWFLAKE_", "RMP_ACCEPTANCE_RUNNER", "__RUNNER_PUBLIC_KEY__"),
        ("SNOWFLAKE_CONSUMER_", "RMP_ACCEPTANCE_READER", "__READER_PUBLIC_KEY__"),
    ]:
        if settings[prefix + "USER"] != user:
            raise ValueError("Config identity differs from the dedicated setup contract")
        path = Path(settings[prefix + "PRIVATE_KEY_PATH"]).resolve()
        if path.parent != directory:
            raise ValueError("Key must be in the same private directory")
        key = serialization.load_pem_private_key(
            path.read_bytes(), password=settings[prefix + "PRIVATE_KEY_PASSPHRASE"].encode()
        )
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("Expected an RSA key of at least 2048 bits")
        public = (
            key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        public = "".join(public.splitlines()[1:-1])
        if not re.fullmatch(r"[A-Za-z0-9+/=]+", public) or sql.count(token) != 1:
            raise ValueError("Invalid public-key/template contract")
        public_keys.append(public)
        sql = sql.replace(token, public)
    if public_keys[0] == public_keys[1]:
        raise ValueError("Reader and runner must have independent keys")
    owner_sql = directory / "owner-setup.sql"
    owner_sql.write_text(sql, encoding="utf-8")
    owner_sql.chmod(0o600)
    return {
        "owner_sql": str(owner_sql),
        "private_config": str(directory / "runtime.json"),
        "executed_sql": False,
        "keys_and_config": "unchanged",
        "note": "Keep this directory private; never upload runtime.json or keys.",
    }


def prepare(directory):
    directory = private_directory(directory)
    directory.mkdir(parents=True, exist_ok=False)
    if os.name == "nt":
        identity = subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"], text=True)
        sid = next(csv.reader([identity.strip()]))[1]
        subprocess.run(
            ["icacls", str(directory), "/inheritance:r", "/grant:r", "*" + sid + ":(OI)(CI)F"],
            check=True,
            capture_output=True,
        )
    else:
        directory.chmod(0o700)
    settings = {"SNOWFLAKE_ACCOUNT": "REPLACE_WITH_ORGANIZATION_ACCOUNT_IDENTIFIER"}
    for prefix, user in [
        ("SNOWFLAKE_", "RMP_ACCEPTANCE_RUNNER"),
        ("SNOWFLAKE_CONSUMER_", "RMP_ACCEPTANCE_READER"),
    ]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        password = secrets.token_urlsafe(32)
        path = directory / (user.lower() + ".p8")
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(password.encode()),
            )
        )
        path.chmod(0o600)
        settings.update(
            {
                prefix + "USER": user,
                prefix + "PRIVATE_KEY_PATH": str(path),
                prefix + "PRIVATE_KEY_PASSPHRASE": password,
            }
        )
    config = directory / "runtime.json"
    config.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    config.chmod(0o600)
    result = refresh_owner_sql(directory)
    result["keys_and_config"] = "new encrypted credentials created"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--refresh-owner-sql", action="store_true")
    args = parser.parse_args()
    action = refresh_owner_sql if args.refresh_owner_sql else prepare
    print(json.dumps(action(args.directory), indent=2))
