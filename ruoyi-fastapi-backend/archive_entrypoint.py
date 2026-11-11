"""Bootstrap only the isolated archive deployment, including its own transport keys."""

import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def configure_transport_keys(path: Path) -> None:
    private = os.environ.get('TRANSPORT_CRYPTO_PRIVATE_KEY')
    public = os.environ.get('TRANSPORT_CRYPTO_PUBLIC_KEY')
    if private or public:
        if not (private and public):
            raise RuntimeError('请同时配置传输加密公钥和私钥，或留空使用本部署的独立密钥')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(pem)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    os.environ['TRANSPORT_CRYPTO_PRIVATE_KEY'] = path.read_text()
    os.environ['TRANSPORT_CRYPTO_PUBLIC_KEY'] = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


def main() -> None:
    for name in ('DB_PASSWORD', 'REDIS_PASSWORD', 'JWT_SECRET_KEY'):
        value = os.environ.get(name, '')
        if not value or value.startswith('replace-with-'):
            raise RuntimeError(f'请为独立部署配置 {name}')
    os.environ['APP_ENV'] = 'archive'
    configure_transport_keys(Path('vf_admin/archive/transport-private.pem'))
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], check=True)
    os.execv(sys.executable, [sys.executable, 'app.py', '--env=archive'])


if __name__ == '__main__':
    main()
