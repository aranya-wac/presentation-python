import asyncio
from app.core.database import init_db
from app.core.security import hash_password
from sqlalchemy import text

async def main():
    await init_db()
    import app.core.database as db_module
    async with db_module._session_factory() as db:
        new_hash = hash_password("Demo@1234")
        await db.execute(
            text("UPDATE users SET hashed_password=:h WHERE email=:e"),
            {"h": new_hash, "e": "demo@wacdeckstudio.com"}
        )
        await db.commit()
        print("Password reset to: Demo@1234")

asyncio.run(main())
