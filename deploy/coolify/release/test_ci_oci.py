import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import ci_oci


class CIImageTests(unittest.TestCase):
    def test_layout_binds_the_index_bytes_and_requires_both_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = Path(tmp)
            (layout / "blobs" / "sha256").mkdir(parents=True)

            def index(platforms: tuple[str, ...]) -> str:
                body = json.dumps(
                    {
                        "schemaVersion": 2,
                        "manifests": [
                            {
                                "platform": {"os": "linux", "architecture": arch},
                                "digest": "sha256:" + "1" * 64,
                            }
                            for arch in platforms
                        ],
                    }
                ).encode()
                digest = hashlib.sha256(body).hexdigest()
                (layout / "blobs" / "sha256" / digest).write_bytes(body)
                (layout / "index.json").write_text(
                    json.dumps(
                        {
                            "manifests": [
                                {
                                    "mediaType": "application/vnd.oci.image.index.v1+json",
                                    "digest": f"sha256:{digest}",
                                    "size": len(body),
                                }
                            ]
                        }
                    )
                )
                return f"sha256:{digest}"

            accepted = index(("amd64", "arm64"))
            ci_oci.verify_layout(layout, accepted)
            with self.assertRaises(ValueError):
                ci_oci.verify_layout(layout, "sha256:" + "0" * 64)
            (layout / "blobs" / "sha256" / accepted.split(":")[1]).write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                ci_oci.verify_layout(layout, accepted)
            for platforms in [("amd64",), ("amd64", "arm64", "arm64")]:
                with self.subTest(platforms=platforms), self.assertRaises(ValueError):
                    ci_oci.verify_layout(layout, index(platforms))

    def test_receipt_requires_exact_main_push_run_and_distinct_artifact_ids(self) -> None:
        source = {
            "repository": "BipoXDXD/GoblinShift",
            "commit": "a" * 40,
            "event": "push",
            "run": "12",
            "attempt": "2",
        }
        images = {
            role: {"artifact": str(index), "digest": "sha256:" + "a" * 64}
            for index, role in enumerate(("app", "executor", "web"), 1)
        }
        self.assertEqual(ci_oci.artifact_ids({"source": source, "images": images}, source), "1,2,3")
        for field, wrong in [
            ("repository", "fork/GoblinShift"),
            ("commit", "b" * 40),
            ("event", "pull_request"),
            ("run", "13"),
            ("attempt", "1"),
        ]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                ci_oci.artifact_ids({"source": {**source, field: wrong}, "images": images}, source)
        for incomplete in [
            {"app": images["app"]},
            {**images, "web": images["app"]},
            {**images, "web": {**images["web"], "artifact": "3,4"}},
        ]:
            with self.subTest(images=incomplete), self.assertRaises(ValueError):
                ci_oci.artifact_ids({"source": source, "images": incomplete}, source)
