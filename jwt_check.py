from datetime import datetime, timedelta, timezone

import jwt

from config import settings

SECRET_KEY = settings.secret_key

payload = {
    "sub": "User1",
    "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    "role": "user",
}

token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

print("--- ENCODED JWT TOKEN ---")
print(token)

decoded_payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])

print("--- DECODED & VERIFIED PAYLOAD ---")
print(decoded_payload)
