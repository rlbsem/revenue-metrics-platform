"""Prepare private local credentials and ONE owner-run SQL file; execute no SQL."""

import argparse
import csv
import json
import os
from pathlib import Path
import secrets
import subprocess

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from revenue_platform.runtime import ROOT


def prepare(directory):
    directory = Path(os.path.abspath(directory)).resolve()
    if directory == ROOT or ROOT in directory.parents:
        raise ValueError("Credential directory must be outside the repository")
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
    sql = (ROOT / "snowflake/bootstrap.sql").read_text(encoding="utf-8")
    sql += "\nUSE ROLE USERADMIN;\n"
    for prefix, user, role in [
        ("SNOWFLAKE_", "RMP_ACCEPTANCE_RUNNER", "RMP_BUILD"),
        ("SNOWFLAKE_CONSUMER_", "RMP_ACCEPTANCE_READER", "RMP_CONSUMER"),
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
        public = (
            key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        public = "".join(public.splitlines()[1:-1])
        # Do not overwrite an existing user's registered key or unknown grants.
        sql += (
            f"CREATE USER {user} TYPE=SERVICE RSA_PUBLIC_KEY='{public}' "
            f"DEFAULT_ROLE={role} DEFAULT_SECONDARY_ROLES=() "
            "DEFAULT_WAREHOUSE=RMP_WH;\n"
        )
        settings.update(
            {
                prefix + "USER": user,
                prefix + "PRIVATE_KEY_PATH": str(path),
                prefix + "PRIVATE_KEY_PASSPHRASE": password,
            }
        )
    sql += "\nUSE ROLE SECURITYADMIN;\n"
    sql += "GRANT ROLE RMP_LOADER TO USER RMP_ACCEPTANCE_RUNNER;\n"
    sql += "GRANT ROLE RMP_BUILD TO USER RMP_ACCEPTANCE_RUNNER;\n"
    sql += "GRANT ROLE RMP_CONSUMER TO USER RMP_ACCEPTANCE_READER;\n"
    (directory / "owner-setup.sql").write_text(sql, encoding="utf-8")
    config = directory / "runtime.json"
    config.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    config.chmod(0o600)
    return {
        "owner_sql": str(directory / "owner-setup.sql"),
        "private_config": str(config),
        "executed_sql": False,
        "note": "Keep this directory private; never upload runtime.json or keys.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    print(json.dumps(prepare(parser.parse_args().directory), indent=2))
