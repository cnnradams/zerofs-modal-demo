# /// script
# requires-python = ">=3.10"
# dependencies = ["modal==1.6.3"]
# ///
"""A Git workspace backed by S3, with storage credentials only in a sidecar."""

import argparse
from pathlib import Path

import modal

VERSION = "2.3.6"
SHA256 = "670620d875b9ff923bec26ec249b22fa46ec03c206a55963f204afbd2eb6a066"
ROOT = Path(__file__).parent


def run(container, *args, **kwargs):
    process = container.exec(*args, **kwargs)
    process.wait()
    print(process.stdout.read(), end="")
    print(process.stderr.read(), end="")
    if process.returncode:
        raise RuntimeError(f"Command failed with exit code {process.returncode}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True, help="Dedicated ZeroFS prefix; reuse to reopen")
    parser.add_argument("--role-arn", required=True, help="AWS role that trusts Modal OIDC")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--secret", default="zerofs-password", help="Modal Secret with ZEROFS_PASSWORD")
    args = parser.parse_args()
    prefix = args.prefix.strip("/")
    if not prefix or any(c in prefix for c in "*?") or any(p in ("", ".", "..") for p in prefix.split("/")):
        parser.error("Use a nonempty prefix without wildcards or dot path components")

    app = modal.App.lookup("zerofs-sidecar-demo", create_if_missing=True)
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("curl", "fuse3", "git")
        .run_commands(
            f"curl -fLsS https://github.com/Barre/ZeroFS/releases/download/v{VERSION}/"
            "zerofs-pgo-multiplatform.tar.gz -o /tmp/zerofs.tar.gz",
            f"echo '{SHA256}  /tmp/zerofs.tar.gz' | sha256sum -c -",
            "tar -xzf /tmp/zerofs.tar.gz -C /tmp zerofs-linux-amd64-pgo",
            "install -m 755 /tmp/zerofs-linux-amd64-pgo /usr/local/bin/zerofs",
            "rm /tmp/zerofs.tar.gz /tmp/zerofs-linux-amd64-pgo",
        )
        .build(app)
    )
    storage_image = image.add_local_file(ROOT / "storage.py", "/opt/storage.py", copy=True).build(app)
    sandbox = modal.Sandbox.create(
        "sleep",
        "1800",
        app=app,
        image=image,
        runtime="vm",
        cloud="aws",
        region=args.region,
        memory=4096,
        timeout=1800,
        include_oidc_identity_token=False,
        experimental_options={"vm_sidecar_memory_reserve_mib": 2048},
    )
    mounted = False
    try:
        storage = sandbox._experimental_sidecars.create(
            "python",
            "/opt/storage.py",
            name="storage",
            image=storage_image,
            env={
                "BUCKET": args.bucket,
                "PREFIX": prefix,
                "AWS_REGION": args.region,
                "AWS_ROLE_ARN": args.role_arn,
            },
            secrets=[modal.Secret.from_name(args.secret, required_keys=["ZEROFS_PASSWORD"])],
            include_oidc_identity_token=True,
        )
        run(
            sandbox,
            "bash",
            "-ec",
            """
mkdir -p /workspace
for i in $(seq 60); do
    if timeout 1 bash -c 'exec 3<>/dev/tcp/storage/5564' 2>/dev/null; then break; fi
    sleep 1
done
setsid nohup zerofs mount --writeback false storage:5564 /workspace \
    >/tmp/zerofs-client.log 2>&1 </dev/null &
for i in $(seq 60); do mountpoint -q /workspace && exit 0; sleep 1; done
cat /tmp/zerofs-client.log
exit 1
""",
            timeout=150,
        )
        mounted = True
        print(f"Mounted s3://{args.bucket}/{prefix} in {sandbox.object_id}")

        # Replace this command with your agent. It sees ordinary files at /workspace.
        run(
            sandbox,
            "bash",
            "-ec",
            """
git init -q -b main repo
cd repo
git config user.name 'ZeroFS demo'
git config user.email 'demo@example.invalid'
printf 'Hello from a Modal sandbox.\n' >> hello.txt
git add hello.txt
git commit -qm 'Update workspace'
git fsck --full
git log --oneline -3
cat hello.txt
""",
            workdir="/workspace",
        )
    finally:
        try:
            if mounted:
                # With --writeback false, completed ordinary writes have reached
                # the sidecar. Flush to S3 without unmounting or closing handles.
                run(storage, "zerofs", "flush", "-c", "/etc/zerofs.toml")
        finally:
            sandbox.terminate()


if __name__ == "__main__":
    with modal.enable_output():
        main()
