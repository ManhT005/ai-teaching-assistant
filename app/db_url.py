"""Build the container DB URL without interpolating raw credentials into a URL string."""

from sqlalchemy.engine import URL


def configure_database(environ):
    """POSTGRES_* is authoritative in containers, including passwords containing URL symbols."""
    for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_HOST"):
        if not environ.get(name):
            raise ValueError(f"Set {name} before starting containers")
    environ["DATABASE_URL"] = URL.create(
        "postgresql+psycopg",
        username=environ["POSTGRES_USER"],
        password=environ["POSTGRES_PASSWORD"],
        host=environ["POSTGRES_HOST"],
        port=int(environ.get("POSTGRES_PORT", "5432")),
        database=environ["POSTGRES_DB"],
    ).render_as_string(hide_password=False)
