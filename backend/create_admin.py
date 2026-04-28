import asyncio
import db
import auth

async def main():
    await db.create_pool()

    user = await db.create_user(
        username="admin",
        email="admin@test.com",
        password_hash=auth.hash_password("admin123"),
        trial_tokens=100000,
        is_verified=True
    )

    user = await db.get_user_by_id(user["id"])
    print("Admin created:", user)

asyncio.run(main())