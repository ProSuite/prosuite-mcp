import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    ssl_cert_path: str | None
    spec_path: str | None


def load_config() -> Config:
    return Config(
        host=os.environ.get("PROSUITE_HOST", "localhost"),
        port=int(os.environ.get("PROSUITE_PORT", "5151")),
        ssl_cert_path=os.environ.get("PROSUITE_SSL_CERT_PATH"),
        spec_path=os.environ.get("PROSUITE_SPEC_PATH"),
    )
