"""
github_sync.py — push tips.csv do GitHub repozitára cez GitHub API.
Používa sa po každom save_tips / settle_tips, aby boli tipy persistentné
aj na Streamlit Community Cloud (ephemeral filesystem).
"""

import base64
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_OWNER = "jurinho21"
_REPO = "futbal_predikcie"
_BRANCH = "main"


def get_github_token() -> str | None:
    """Načíta GitHub token zo Streamlit secrets alebo env premennej."""
    try:
        import streamlit as st
        t = st.secrets.get("GITHUB_TOKEN")
        if t:
            return str(t)
    except Exception:
        pass
    import os
    return os.environ.get("GITHUB_TOKEN")


def push_tips_csv(data_dir: Path, token: str) -> bool:
    """
    Pushne data_dir/tips.csv do GitHub repozitára.
    data_dir musí byť relatívna cesta od root repozitára (napr. data/eredivisie).
    Vracia True pri úspechu.
    """
    tips_csv = Path(data_dir) / "tips.csv"
    if not tips_csv.exists():
        return False

    repo_path = (Path(data_dir) / "tips.csv").as_posix()
    content_b64 = base64.b64encode(tips_csv.read_bytes()).decode()

    api_url = f"https://api.github.com/repos/{_OWNER}/{_REPO}/contents/{repo_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    r = requests.get(api_url, headers=headers, timeout=10)
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload: dict = {
        "message": f"auto: update {repo_path}",
        "content": content_b64,
        "branch": _BRANCH,
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if r.status_code in (200, 201):
        logger.info("GitHub sync OK: %s", repo_path)
        return True
    logger.warning("GitHub sync zlyhala (%d): %s", r.status_code, r.text[:200])
    return False
