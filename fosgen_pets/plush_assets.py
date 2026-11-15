"""Closed, checksum-checked original v3 masters; never caller-selected files."""
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

ASSET_ROOT = Path(__file__).with_name("assets") / "v3"
# Reviewed v3 catalog and anchors are immutable. Any replacement needs v4.
CATALOG_SHA256 = "2ca34107eeb71abe8f1edd99008a7d96e42f4f703d259b92c51b5a9de56b9083"


def catalog() -> dict:
    raw = (ASSET_ROOT / "anchor-data.json").read_bytes()
    if len(raw) > 65536 or sha256(raw).hexdigest() != CATALOG_SHA256:
        raise ValueError("The released plush catalog checksum does not match")
    data = json.loads(raw)
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported plush catalog")
    return data["species"]


@lru_cache(maxsize=4)
def _asset_bytes(root: str, species: str, checksum: str) -> bytes:
    path = Path(root) / f"{species}-master.webp"
    if path.stat().st_size > 524288:
        raise ValueError("Plush master exceeds its bounded size")
    data = path.read_bytes()
    if sha256(data).hexdigest() != checksum:
        raise ValueError("Plush master checksum mismatch")
    return data


def load_master(species: str) -> tuple[Image.Image, dict]:
    records = catalog()
    if not isinstance(species, str) or species not in records:
        raise ValueError("No plush master for species")
    record = records[species]
    if record["imagefile"] != f"{species}-master.webp":
        raise ValueError("Invalid plush master filename")
    data = _asset_bytes(str(ASSET_ROOT), species, record["sha256"])
    with Image.open(BytesIO(data)) as image:
        if image.size != (512, 512) or image.format != "WEBP":
            raise ValueError("Plush masters must be 512px WebP")
        # Every caller owns its image and decoded pixels, including concurrent callers.
        master = image.convert("RGBA")
    if master.getchannel("A").getextrema() != (0, 255):
        raise ValueError("Plush master must retain its transparent silhouette")
    return master, record


def normalized_anchors(record: dict) -> dict:
    def box(name):
        return tuple(value / 512 for value in record[name])
    eyes = tuple(((b[0] + b[2]) / 1024, (b[1] + b[3]) / 1024) for b in record["eye_boxes"])
    noses = record["nose_boxes"]
    feet = record["feetpoints"]
    # Anatomical left/right front feet: intermediate points for four-legged masters.
    selected = (feet[len(feet) // 2 - 1], feet[len(feet) // 2]) if len(feet) >= 4 else (feet[0], feet[1])
    result = {name: box(name) for name in ("bbox", "head", "body")}
    result["tail"] = box("tail") if record.get("tail") else (.85, .70, .86, .71)
    result["eyes"] = eyes
    result["nose"] = tuple(sum((b[i] + b[i + 2]) / 1024 for b in noses) / len(noses) for i in (0, 1))
    result["feet"] = tuple((point[0] / 512, point[1] / 512) for point in selected)
    return result
