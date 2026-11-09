# S3-backed workspaces on Modal

Give an agent a mounted filesystem at `/workspace`, backed by your S3 bucket.
[ZeroFS](https://github.com/Barre/ZeroFS) stores the files and filesystem metadata
in S3. AWS credentials stay in a private sidecar.

```text
Agent sandbox /workspace → ZeroFS sidecar → S3
```

## Run

You need [uv](https://docs.astral.sh/uv/), a Modal account, an S3 bucket, and an
AWS role that [trusts your Modal workspace's OIDC identity](https://modal.com/docs/guide/oidc-integration).
Scope the role to a dedicated bucket prefix, allowing `ListBucket`, `GetObject`,
`PutObject`, `DeleteObject`, `AbortMultipartUpload`, and `ListMultipartUploadParts`.

```bash
git clone https://github.com/cnnradams/zerofs-modal-demo.git
cd zerofs-modal-demo
uvx --from modal==1.6.3 modal setup

# Create once; keep this password to reopen the filesystem.
uvx --from modal==1.6.3 modal secret create zerofs-password \
  ZEROFS_PASSWORD="$(openssl rand -hex 32)"

uv run demo.py \
  --bucket YOUR_BUCKET \
  --prefix zerofs-demo \
  --role-arn arn:aws:iam::YOUR_ACCOUNT:role/YOUR_ROLE \
  --region us-east-1
```

The demo creates a Git commit under `/workspace`, flushes to S3, and stops the
sandbox. Run it again with the same prefix and secret: a fresh sandbox reopens
the repository and adds another commit. Data stays in S3.

Replace the Git command in [demo.py](demo.py) with your agent.
[storage.py](storage.py) starts ZeroFS in the sidecar and obtains temporary AWS
credentials using Modal OIDC. The agent receives neither the AWS credentials
nor the encryption password. No separate database or custom kernel is needed.

## Save to S3

From your Python controller, ask the sidecar to flush. Here, `storage` is the
sidecar handle returned by `sandbox._experimental_sidecars.create(...)` in
[demo.py](demo.py):

```python
flush = storage.exec("zerofs", "flush", "-c", "/etc/zerofs.toml")
flush.wait()
if flush.returncode:
    raise RuntimeError(flush.stderr.read())
```

A successful flush means the preceding completed filesystem writes are persisted
to S3. The agent can keep running with files open; no unmount is needed. This
relies on the demo's `--writeback false` mount setting.

Flush after a task or periodically. Later writes need another flush. Data still
in application buffers or dirty memory mappings must be flushed by the client
first. For a checkpoint between complete Git operations, flush at a task boundary.


Real-S3 tests passed Git clone/commit/merge/push/gc/fsck and recovery in a fresh
sandbox after flushing and killing ZeroFS. Flushing while a file remained open,
then writing through the same handle, also passed. This is a demo, not a full
filesystem qualification.
