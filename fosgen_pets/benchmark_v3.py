"""CPU/RSS smoke measurement in a fresh process, never a production DB.

Run: python -m fosgen_pets.benchmark_v3 [--rounds 2] [--workers 2]
RSS is the whole process high-water mark, including Python/NumPy/Pillow.
"""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import argparse
import json
import platform
from pathlib import Path
import resource
import statistics
import sys
import time

import numpy as np
import PIL

from .core import V3_SPECIES, build_appearance
from .portrait import render_portrait


def benchmark(*, rounds=1, workers=2):
    if type(rounds) is not int or not 1 <= rounds <= 10:
        raise ValueError("rounds must be in 1..10")
    if type(workers) is not int or not 1 <= workers <= 2:
        raise ValueError("workers must be in 1..2, matching production render slots")
    candidates = [build_appearance(species, f"benchmark:{index}", asset_version=3)
                  for index in range(rounds) for species in V3_SPECIES]

    def render(dna):
        start = time.perf_counter()
        portrait = render_portrait(dna)
        return (time.perf_counter() - start, len(portrait.png),
                sha256(portrait.png).hexdigest(), portrait.pixel_hash)

    start_wall, start_cpu = time.perf_counter(), time.process_time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outputs = list(pool.map(render, candidates))
    elapsed, cpu = time.perf_counter() - start_wall, time.process_time() - start_cpu
    durations = sorted(row[0] for row in outputs)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    resource_rss_bytes = int(rss if sys.platform == "darwin" else rss * 1024)
    rss_bytes = resource_rss_bytes
    rss_method = "getrusage(RUSAGE_SELF)"
    if sys.platform.startswith("linux"):
        # Linux getrusage can retain the parent's pre-exec high-water mark.
        # VmHWM belongs to this executable's address space, so a large test
        # runner must not be attributed to the fresh rendering subprocess.
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                rss_bytes = int(line.split()[1]) * 1024
                rss_method = "Linux /proc/self/status VmHWM"
                break
    return {
        "render_count": len(outputs), "workers": workers, "rounds": rounds,
        "wall_seconds": elapsed, "cpu_seconds": cpu,
        "mean_render_seconds": statistics.mean(durations),
        "p95_render_seconds": durations[max(0, int(len(durations) * .95) - 1)],
        "process_peak_rss_bytes": rss_bytes,
        "process_peak_measurement": rss_method,
        "resource_high_water_rss_bytes": resource_rss_bytes,
        "png_size_range_bytes": [min(row[1] for row in outputs), max(row[1] for row in outputs)],
        "distinct_pngs": len({row[2] for row in outputs}),
        "distinct_pixel_hashes": len({row[3] for row in outputs}),
        "python": platform.python_version(), "platform": platform.platform(),
        "pillow": PIL.__version__, "numpy": np.__version__,
        "scope": "Local CPU smoke measurement; repeat on the target Bothost plan",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(benchmark(rounds=args.rounds, workers=args.workers), indent=2))


if __name__ == "__main__":
    main()
