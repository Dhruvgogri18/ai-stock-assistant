"""Download the market database on first start (it is too big for git).

Set DB_DOWNLOAD_URL to a .db or .db.gz file (e.g. a GitHub Release asset). No-op if the DB exists.
"""
import gzip, os, shutil, sys, urllib.request
from pathlib import Path

from backend.app.config.settings import settings

dest = Path(settings.warehouse_path)
url = os.environ.get("DB_DOWNLOAD_URL", "").strip()
if dest.exists() and dest.stat().st_size > 1_000_000:
    print(f"[bootstrap] {dest} already present"); sys.exit(0)
if not url:
    print("[bootstrap] no DB_DOWNLOAD_URL set; starting with an empty database"); sys.exit(0)
dest.parent.mkdir(parents=True, exist_ok=True)
tmp = dest.with_suffix(".download")
print(f"[bootstrap] downloading {url}")
urllib.request.urlretrieve(url, tmp)
if url.endswith(".gz"):
    with gzip.open(tmp, "rb") as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out)
    tmp.unlink()
else:
    tmp.replace(dest)
print(f"[bootstrap] ready: {dest} ({dest.stat().st_size // 1_000_000} MB)")
