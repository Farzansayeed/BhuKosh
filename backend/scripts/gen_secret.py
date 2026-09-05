"""Print a strong random secret for JWT_SECRET (paste into .env)."""
import secrets

print(secrets.token_urlsafe(48))
