"""Plan or fetch two pinned Apple SEC HTML mirrors, with exact SHA verification.

No arbitrary URL argument. Without --download this performs no network or writes.
Generated source-manifest.json contains portable filenames, not machine paths.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "107d182146ccf5d22a4e71f82cde8b363b212cf1"
SOURCE_PREFIX = f"https://raw.githubusercontent.com/dgunning/edgartools/{REVISION}/tests/fixtures/attachments/aapl/20250329/"
FILENAMES = {"parser_probe_aapl-20250329.htm": "aapl-20250329.htm", "parser_probe_R2.htm": "R2.htm"}


def load_manifest():
    rows = json.loads((ROOT / "data/evaluation/parser_fixture_manifest.json").read_text())
    if not isinstance(rows, list) or len(rows) != 2 or {r.get("name") for r in rows} != set(FILENAMES):
        raise ValueError("Expected exactly the two fixed parser fixtures")
    for row in rows:
        if (row.get("repository_revision") != REVISION or
                row.get("download_url") != SOURCE_PREFIX + FILENAMES[row["name"]] or
                type(row.get("bytes")) is not int or not 0 < row["bytes"] < 2_000_000 or
                not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", ""))):
            raise ValueError("Parser fixture manifest differs from the fixed source contract")
    if sum(r["bytes"] for r in rows) >= 2_000_000:
        raise ValueError("Parser fixture download exceeds the total size cap")
    return rows


def verify_bytes(raw, record):
    if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise ValueError("Fixture size/SHA256 mismatch: " + record["name"])


class PinnedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl not in {SOURCE_PREFIX + value for value in FILENAMES.values()}:
            raise ValueError("Unexpected redirect outside the fixed fixture URLs")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true", help="Explicitly allow downloading missing fixed fixtures")
    args = parser.parse_args(argv)
    records = load_manifest()
    if not args.download:
        print(json.dumps({"mode": "plan_only", "network_requests": 0, "writes": 0,
                          "total_bytes": sum(r["bytes"] for r in records),
                          "output_dir": str(args.output_dir), "fixtures": records,
                          "next_step": "Repeat with --download to fetch and verify the two fixed files."}, indent=2))
        return
    output = args.output_dir.resolve()
    # Validate every existing target before any downloads; never silently trust
    # an old cache or overwrite a different file under the fixture's name.
    for record in records:
        target = output / record["name"]
        if not target.resolve().is_relative_to(output):
            raise ValueError("Fixture destination escapes the output directory")
        if target.exists():
            if not target.is_file():
                raise ValueError("Fixture destination is not a regular file")
            with target.open("rb") as stream:
                verify_bytes(stream.read(record["bytes"] + 1), record)
    output.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(PinnedRedirects())
    generated = []
    requests = 0
    for record in records:
        target = output / record["name"]
        acquisition = "verified_existing"
        if not target.exists():
            request = urllib.request.Request(record["download_url"], headers={"User-Agent": "VeriFin-parser-fixture-reproduction"})
            with opener.open(request, timeout=30) as response:
                raw = response.read(record["bytes"] + 1)
            requests += 1
            verify_bytes(raw, record)
            with tempfile.NamedTemporaryFile(mode="wb", dir=output, delete=False) as stream:
                stream.write(raw)
                temporary = Path(stream.name)
            temporary.replace(target)
            acquisition = "downloaded_and_verified"
        generated.append({**record, "acquisition": acquisition,
                          "verified_at": datetime.now(timezone.utc).isoformat()})
    (output / "source-manifest.json").write_text(json.dumps(generated, indent=2) + "\n")
    print(json.dumps({"mode": "verified", "network_requests": requests,
                      "files": [r["name"] for r in generated], "source_manifest": "source-manifest.json",
                      "total_bytes": sum(r["bytes"] for r in generated)}, indent=2))


if __name__ == "__main__":
    main()
