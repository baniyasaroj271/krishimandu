import os

from app import app


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5001")),
        ssl_context="adhoc" if os.getenv("DEV_HTTPS", "1") == "1" else None,
    )
