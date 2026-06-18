from py_vapid import Vapid01
from cryptography.hazmat.primitives import serialization
import base64

vapid = Vapid01()
vapid.generate_keys()

# Public key
public_key = vapid.public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)

# Private key
private_key = vapid.private_key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

print(
    "VAPID_PUBLIC_KEY=" +
    base64.urlsafe_b64encode(public_key).decode().rstrip("=")
)

print(
    "VAPID_PRIVATE_KEY=" +
    base64.urlsafe_b64encode(private_key).decode().rstrip("=")
)