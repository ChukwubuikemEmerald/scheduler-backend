from config import settings

print("Database configured:", bool(settings.database_url))
print("Secret configured:", bool(settings.secret_key))