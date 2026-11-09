"""Trusted sidecar: ZeroFS exchanges Modal's OIDC token for AWS credentials."""

import os
from pathlib import Path

# Use ZeroFS's built-in AWS web-identity provider; no custom STS client needed.
os.umask(0o077)
token_file = "/run/modal-oidc-token"
Path(token_file).write_text(os.environ.pop("MODAL_IDENTITY_TOKEN"))
os.environ["AWS_WEB_IDENTITY_TOKEN_FILE"] = token_file
os.environ["AWS_ROLE_SESSION_NAME"] = "zerofs-demo"

# ZeroFS expands these environment references inside the sidecar.
Path("/etc/zerofs.toml").write_text("""
[cache]
dir = "/var/cache/zerofs"
disk_size_gb = 1.0
memory_size_gb = 0.25

[storage]
url = "s3://${BUCKET}/${PREFIX}"
encryption_password = "${ZEROFS_PASSWORD}"

[servers.rpc]
addresses = ["127.0.0.1:7000"]

[servers.ninep]
addresses = ["0.0.0.0:5564"]
""")
os.execvp("zerofs", ["zerofs", "run", "-c", "/etc/zerofs.toml"])
