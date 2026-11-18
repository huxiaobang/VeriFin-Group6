"""Bounded parallel ranges for a fixed, public Hugging Face LFS file.

No user token. Verifies every HTTP range and the official final SHA256.
Only moves a complete, verified file to the requested destination.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import time

os.environ["HF_HUB_DISABLE_XET"] = "1"
from huggingface_hub import HfApi, get_hf_file_metadata, hf_hub_url
from huggingface_hub.utils import get_session


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            result.update(block)
    return result.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--destination", required=True, type=Path)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--part-mib", type=int, default=4)
    args = p.parse_args()
    if not re.fullmatch(r"[a-f0-9]{40}", args.revision) or not 1 <= args.workers <= 8 or not 1 <= args.part_mib <= 32:
        raise ValueError("A fixed commit, 1-8 workers and 1-32 MiB parts are required")
    info = HfApi(token=False).model_info(args.repo, revision=args.revision, files_metadata=True)
    entry = next(x for x in info.siblings if x.rfilename == args.file)
    if not entry.lfs or not re.fullmatch(r"[a-f0-9]{64}", entry.lfs.sha256):
        raise ValueError("The official file has no verified LFS SHA256")
    expected_hash, size = entry.lfs.sha256, entry.size
    metadata = get_hf_file_metadata(hf_hub_url(args.repo, args.file, revision=args.revision), token=False)
    if metadata.size != size or metadata.commit_hash != args.revision:
        raise ValueError("Official revision/size metadata disagree")
    destination = args.destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size == size and digest(destination) == expected_hash:
        print(json.dumps({"phase": "already_verified", "file": str(destination), "sha256": expected_hash}), flush=True)
        return
    parts = destination.parent / ".range-parts" / expected_hash
    parts.mkdir(parents=True, exist_ok=True)
    part_bytes = args.part_mib * 1024 * 1024
    segments = [(i, offset, min(offset + part_bytes, size)-1) for i, offset in enumerate(range(0, size, part_bytes))]
    started = time.monotonic()
    print(json.dumps({"phase": "start", "pid": os.getpid(), "bytes": size, "workers": args.workers,
                      "part_count": len(segments), "official_sha256": expected_hash}), flush=True)

    def fetch(segment):
        i, start, end = segment
        path = parts / f"{i:06d}.part"
        length = end-start+1
        if path.exists() and path.stat().st_size == length:
            return length
        temp = path.with_suffix(".incomplete")
        for attempt in range(3):
            try:
                with get_session().stream("GET", metadata.location, headers={"Range": f"bytes={start}-{end}"},
                                          follow_redirects=True, timeout=60) as response:
                    if response.status_code in (401, 403):
                        raise PermissionError(f"Public file access rejected: {response.status_code}")
                    response.raise_for_status()
                    if response.status_code != 206 or response.headers.get("content-range") != f"bytes {start}-{end}/{size}":
                        raise ValueError("Server did not honor the exact requested byte range")
                    received = 0
                    with temp.open("wb") as f:
                        for block in response.iter_bytes(262144):
                            received += len(block)
                            if received > length:
                                raise ValueError("Range response exceeded declared length")
                            f.write(block)
                    if received != length:
                        raise ValueError("Range response was incomplete")
                    temp.replace(path)
                    return received
            except (PermissionError, ValueError):
                raise
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)

    completed = 0
    failed = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, segment):segment[0] for segment in segments}
        for number, future in enumerate(as_completed(futures), 1):
            try:
                completed += future.result()
            except Exception as error:
                failed.append(futures[future])
                # Keep progress observable while already-queued ranges finish.
                # Never log the signed URL that an HTTP exception may contain.
                print(json.dumps({"phase":"part_failed","part":futures[future],
                                  "error_type":type(error).__name__}),flush=True)
            if number % 16 == 0 or number == len(segments):
                print(json.dumps({"phase": "progress", "parts": number, "total_parts": len(segments),
                                  "bytes": completed, "failed_parts":len(failed),
                                  "elapsed_seconds": round(time.monotonic()-started, 1)}), flush=True)
    if failed:
        raise RuntimeError(f"{len(failed)} ranges failed; re-run with the same arguments to resume validated-size parts")
    assembled = destination.with_suffix(destination.suffix + ".verified-incomplete")
    with assembled.open("wb") as out:
        for i, _, _ in segments:
            with (parts / f"{i:06d}.part").open("rb") as source:
                for block in iter(lambda: source.read(1048576), b""):
                    out.write(block)
    actual = digest(assembled)
    if assembled.stat().st_size != size or actual != expected_hash:
        raise ValueError("Assembled file does not match the official LFS size/SHA256")
    assembled.replace(destination)
    record = {"phase": "complete", "repo": args.repo, "revision": args.revision, "file": args.file,
              "destination": str(destination), "bytes": size, "sha256": actual, "official_sha256_verified": True,
              "elapsed_seconds": time.monotonic()-started, "workers": args.workers, "authentication": False}
    destination.with_suffix(destination.suffix + ".download.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)


if __name__ == "__main__":
    main()
