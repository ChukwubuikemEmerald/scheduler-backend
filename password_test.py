from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()

plain_password = "MyPassword123"

hashed_password = password_hash.hash(plain_password)

print("Hash:")
print(hashed_password)

print("Correct password:")
print(password_hash.verify("MyPassword123", hashed_password))

print("Wrong password:")
print(password_hash.verify("WrongPassword", hashed_password))