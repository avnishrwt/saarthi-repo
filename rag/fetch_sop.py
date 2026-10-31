"""
Download the official SOP documents listed in data/sop/sources.json into data/sop/.

    .venv\\Scripts\\python -m rag.fetch_sop

The PDFs are large, so they are not in git; each teammate runs this once.
A download that is not a real PDF (e.g. an HTML error page) is rejected.
"""
import json
import sys

import httpx

from shared import config

SOP = config.DATA_DIR / "sop"


def main() -> int:
    sources = json.loads((SOP / "sources.json").read_text(encoding="utf-8"))
    ok = True
    for doc, meta in sources.items():
        if doc.startswith("_"):
            continue
        out = SOP / f"{doc}.pdf"
        if out.exists() and out.read_bytes()[:5] == b"%PDF-":
            print(f"already here: {out.name}")
            continue
        print(f"downloading {meta['title']} ({meta['year']}) ...")
        try:
            r = httpx.get(meta["url"], timeout=180, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as e:
            print(f"  failed: {e}")
            ok = False
            continue
        if r.content[:5] != b"%PDF-":
            print("  failed: the server did not return a PDF (check the URL in sources.json)")
            ok = False
            continue
        out.write_bytes(r.content)
        print(f"  saved {out.name} ({len(r.content) // 1024} KB)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
