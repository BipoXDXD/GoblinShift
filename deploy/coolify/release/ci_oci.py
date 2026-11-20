"""Verify the OCI index and bind release artifacts to the exact successful main CI."""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

ROLES = ("app", "executor", "web")
INDEX = "application/vnd.oci.image.index.v1+json"
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")


def verify_layout(layout: Path, digest: str) -> None:
    if not DIGEST.fullmatch(digest):
        raise ValueError("Invalid OCI index digest")
    descriptors = json.loads((layout / "index.json").read_text())["manifests"]
    if len(descriptors) != 1:
        raise ValueError("Expected exactly one OCI index descriptor")
    descriptor = descriptors[0]
    if descriptor.get("mediaType") != INDEX or descriptor.get("digest") != digest:
        raise ValueError("OCI layout index differs from build output")
    body = (layout / "blobs" / "sha256" / digest.removeprefix("sha256:")).read_bytes()
    if len(body) != descriptor.get("size"):
        raise ValueError("OCI index bytes differ from descriptor")
    platform_digests(body, digest)


def platform_digests(body: bytes, digest: str) -> dict[str, str]:
    if f"sha256:{hashlib.sha256(body).hexdigest()}" != digest:
        raise ValueError("OCI index bytes differ from approved digest")
    index = json.loads(body)
    if not isinstance(index, dict) or index.get("schemaVersion") != 2:
        raise ValueError("Invalid OCI index")
    manifests = index.get("manifests")
    if not isinstance(manifests, list):
        raise ValueError("OCI index requires a list of manifests")
    platforms: dict[str, str] = {}
    for item in manifests:
        platform = item.get("platform") if isinstance(item, dict) else None
        if not isinstance(platform, dict):
            raise ValueError("Invalid OCI manifest platform")
        arch = platform.get("architecture")
        if platform.get("os") == arch == "unknown":  # BuildKit provenance descriptors.
            continue
        child = item.get("digest")
        if (
            platform.get("os") != "linux"
            or arch not in ("amd64", "arm64")
            or arch in platforms
            or not isinstance(child, str)
            or not DIGEST.fullmatch(child)
        ):
            raise ValueError("Invalid or duplicate OCI runtime manifest")
        platforms[arch] = child
    if set(platforms) != {"amd64", "arm64"}:
        raise ValueError("OCI index requires both linux/amd64 and linux/arm64")
    return platforms


def artifact_ids(receipt: Any, source: dict[str, str]) -> str:
    if set(receipt) != {"source", "images"} or receipt["source"] != source:
        raise ValueError("OCI receipt was not produced by this exact CI run and attempt")
    images = receipt["images"]
    if set(images) != set(ROLES):
        raise ValueError("Expected an artifact per release image")
    ids = []
    for role in ROLES:
        image = images[role]
        if (
            set(image) != {"artifact", "digest"}
            or not re.fullmatch(r"[1-9][0-9]*", image["artifact"])
            or not DIGEST.fullmatch(image["digest"])
        ):
            raise ValueError(f"Invalid OCI receipt for {role}")
        ids.append(image["artifact"])
    if len(set(ids)) != len(ROLES):
        raise ValueError("Each release image needs a distinct artifact")
    return ",".join(ids)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    verify = subcommands.add_parser("verify")
    verify.add_argument("path", type=Path)
    verify.add_argument("digest")
    amd64 = subcommands.add_parser("amd64")
    amd64.add_argument("path", type=Path)
    amd64.add_argument("digest")
    for name in ("record", "release"):
        subcommands.add_parser(name).add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "verify":
        verify_layout(args.path, args.digest)
        return
    if args.command == "amd64":
        print(platform_digests(args.path.read_bytes(), args.digest)["amd64"])
        return
    env = os.environ
    if args.command == "record":
        source = {
            "repository": env["GITHUB_REPOSITORY"],
            "commit": env["GITHUB_SHA"],
            "event": env["GITHUB_EVENT_NAME"],
            "run": env["GITHUB_RUN_ID"],
            "attempt": env["GITHUB_RUN_ATTEMPT"],
        }
        receipt = {
            "source": source,
            "images": {
                role: {
                    "artifact": env[f"{role.upper()}_ARTIFACT"],
                    "digest": env[f"{role.upper()}_DIGEST"],
                }
                for role in ROLES
            },
        }
        artifact_ids(receipt, source)
        args.path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
        return
    source = {
        "repository": env["GITHUB_REPOSITORY"],
        "commit": env["RELEASE_COMMIT"],
        "event": "push",
        "run": env["CI_RUN_ID"],
        "attempt": env["CI_RUN_ATTEMPT"],
    }
    receipt = json.loads(args.path.read_text())
    ids = artifact_ids(receipt, source)
    with open(env["GITHUB_OUTPUT"], "a") as output:
        output.write(f"ids={ids}\n")
        for role in ROLES:
            output.write(f"{role}_index={receipt['images'][role]['digest']}\n")


if __name__ == "__main__":
    main()
