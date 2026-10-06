from pwdlib import PasswordHash
from sqlalchemy import text

from database import engine

password_hash = PasswordHash.recommended()

new_password = "password"
hashed_password = password_hash.hash(new_password)

with engine.begin() as connection:
    connection.execute(
        text("""
            UPDATE users
            SET hashed_password = :hashed_password
            WHERE username = :username
        """),
        {
            "hashed_password": hashed_password,
            "username": "aj",
        },
    )

print("Admin password reset.")
