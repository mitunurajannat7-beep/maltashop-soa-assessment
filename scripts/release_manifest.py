"""Record immutable local image IDs; this does not deploy to production."""
from pathlib import Path
import json
import os
import re
import subprocess


def main():
    revision=os.environ["BUILD_TAG"]
    if not re.fullmatch(r"[0-9a-f]{40}",revision):
        raise SystemExit("BUILD_TAG must be the complete source commit SHA")
    images=[]
    for role in ("order","inventory","payment","notification"):
        tag=f"maltashop-ci-{role}:{revision}"
        result=json.loads(subprocess.check_output(["docker","image","inspect",tag],text=True))[0]
        if result["Config"].get("Labels",{}).get("org.opencontainers.image.revision")!=revision:
            raise SystemExit("Image revision label mismatch")
        images.append({"service":role,"tag":tag,"image_id":result["Id"],"source_revision":revision})
    destination=Path("ci-results")
    destination.mkdir(exist_ok=True)
    (destination/"release-manifest.json").write_text(json.dumps({
        "source_revision":revision,"images":images,"production_deployed":False,
        "note":"Local Docker image IDs, not registry manifest digests. Proposed production promotion requires a registry and deployment environment."
    },indent=2))


if __name__=="__main__":
    main()
