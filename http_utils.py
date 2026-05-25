"""Zdieľaná HTTP session s automatickým retry pre všetky scrapers."""

import requests
from requests.adapters import HTTPAdapter, Retry

_RETRY = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)


def make_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# Singleton — všetky scrapers zdieľajú jednu session (connection pooling)
SESSION = make_session()
