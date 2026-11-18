"""Fetch checksum-pinned official QA data. Default is a plan; --download opts in.

Standard library only; no accounts/API keys. Downloads and selected archive
members total under 100 MB. Existing mismatching files are never overwritten.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISIONS = {"finqa": "0f16e2867befa6840783e58be38c9efb9229d742", "convfinqa": "cf3eed2d5984960bf06bb8145bcea5e80b0222a6", "tatqa": "870accc41953dcde885aabeb963d94aabdc0fbc3"}
REPOS = {"finqa": "czyssrs/FinQA", "convfinqa": "czyssrs/ConvFinQA", "tatqa": "NExTplusplus/TAT-QA"}
FILES = {
    "finqa": [
        ("dataset/dev.json", 10954658, "a847fb7e0d61a3125a1e2909852df6b89f1ee64d2c5ff1bf689e332214deee51"),
        ("LICENSE", 1067, "92c0e67bd762c15c07ba60de09558338315a8ebf99aac4f51f487179809ac087")],
    "convfinqa": [
        ("data.zip", 17512756, "d764271fae60d81b62e6d58dfc481807ebc8cfbcd633811241723c4a2101072a"),
        ("LICENSE", 1067, "5c7b6fe25d37f4b6cc7e0e76267bce7146765b7661d4bb0e9ecc4eb0ed34fb4c")],
    "tatqa": [
        ("dataset_raw/tatqa_dataset_dev.json", 1637431, "8da095a819af6db3c14877c6df2d4d29960e41d1a63dd1fa853507bd2a616af5"),
        ("dataset_raw/tatqa_dataset_test_gold.json", 2167546, "c4d08418359c1d76468dec420ee748a37f48c06b63cb8ec2766f19d5d314b597"),
        ("README.md", 4689, "85c398173d771a2531abea5b81f3696914296642da2af78c2c34545715c14d5f"),
        ("LICENSE", 1068, "46e27ffbc49c3fd44a9595c2213f8a5f4319a81b53238e49ce1eec39e7e25662")],
}
MEMBER = ("data/dev_turn.json", "dev_turn.json", 21633236, "8a2461be571a3277c443e2dce6e3cce6f4df197db5b7d0b60eed3477f4c952b9")


def verify(data, size, expected, label):
    if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Size/SHA-256 mismatch; refused: " + label)
    return data


def get_file(path, url, size, expected, *, download):
    if path.exists():
        return verify(path.read_bytes(), size, expected, str(path)), "verified_existing"
    if not download:
        raise ValueError("Missing pinned file in --verify-only mode: " + str(path))
    request = Request(url, headers={"User-Agent": "VeriFin-public-benchmark-reproduction/1.0", "Accept": "*/*"})
    with urlopen(request, timeout=60) as response:
        raw = response.read(size + 1)
    verify(raw, size, expected, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    return raw, "downloaded_and_verified"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=[*FILES, "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=Path("work/public-benchmarks"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--download", action="store_true")
    mode.add_argument("--verify-only", action="store_true", help="No network, no writes; verify existing pinned data")
    args = parser.parse_args()
    names = list(FILES) if args.dataset == "all" else [args.dataset]
    planned = [{"dataset": name, "path": name + "/" + path, "url": f"https://raw.githubusercontent.com/{REPOS[name]}/{REVISIONS[name]}/{path}",
                "bytes": size, "sha256": sha, "revision": REVISIONS[name]} for name in names for path, size, sha in FILES[name]]
    total = sum(r["bytes"] for r in planned) + (MEMBER[2] if "convfinqa" in names else 0)
    if total > 100_000_000:
        raise ValueError("Pinned acquisition exceeds 100 MB")
    if not args.download and not args.verify_only:
        print(json.dumps({"mode": "plan_only", "output_dir": str(args.output_dir.resolve()), "download_plus_selected_extraction_bytes": total,
                          "files": planned, "notice": "Use --download to fetch. Preserve OPEN_SOURCE_DATASETS_2026-11-06.NOTICE.txt and original LICENSE files."}, indent=2))
        return 0
    records = []
    for row in planned:
        raw, status = get_file(args.output_dir / row["path"], row["url"], row["bytes"], row["sha256"], download=args.download)
        records.append({**row, "status": status})
    if "convfinqa" in names:
        member, filename, size, expected = MEMBER
        path = args.output_dir / "convfinqa" / filename
        if path.exists():
            verify(path.read_bytes(), size, expected, str(path))
        elif args.verify_only:
            raise ValueError("Missing extracted member in --verify-only mode: " + str(path))
        else:
            # Never extractall(): only this fixed member, with a prechecked size.
            with zipfile.ZipFile(args.output_dir / "convfinqa/data.zip") as archive:
                if archive.getinfo(member).file_size != size:
                    raise ValueError("Unexpected archive member size")
                raw = verify(archive.read(member), size, expected, member)
            with path.open("xb") as stream:
                stream.write(raw)
        records.append({"dataset": "convfinqa", "archive_member": member, "path": "convfinqa/" + filename, "bytes": size, "sha256": expected})
    result = {"protocol": "pinned-public-qa-acquisition-v1", "verified_at": datetime.now(timezone.utc).isoformat(),
              "files": records, "download_plus_selected_extraction_bytes": total,
              "network_enabled": args.download, "paid_api_calls": 0}
    if args.download:
        notice = ROOT / "docs/OPEN_SOURCE_DATASETS_2026-11-06.NOTICE.txt"
        target = args.output_dir / notice.name
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(notice.read_bytes())
        # The source files above remain immutable; this is a fresh receipt.
        receipt = args.output_dir / ("acquisition-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        with receipt.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"verified_files": len(records), "data_dir": str(args.output_dir.resolve()),
                      "download_plus_selected_extraction_bytes": total, "network_enabled": args.download}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print("Public data acquisition: " + str(exc), file=__import__("sys").stderr)
        raise SystemExit(2)
