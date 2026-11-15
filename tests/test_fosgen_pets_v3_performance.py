"""Coarse resource ceiling; precise timings are reported, not CI SLAs."""
import json
import subprocess
import sys
import unittest


@unittest.skipUnless(sys.platform.startswith("linux"), "Bothost resource units are Linux RSS")
class PlushResourceSmokeTests(unittest.TestCase):
    def test_fresh_cpu_process_renders_twelve_species_with_bounded_memory(self):
        result = subprocess.run([sys.executable, "-m", "fosgen_pets.benchmark_v3"],
                                capture_output=True, text=True, timeout=60, check=True)
        metrics = json.loads(result.stdout)
        self.assertEqual(metrics["render_count"], 12)
        self.assertEqual(metrics["workers"], 2)
        self.assertEqual(metrics["distinct_pngs"], 12)
        self.assertEqual(metrics["distinct_pixel_hashes"], 12)
        self.assertEqual(metrics["process_peak_measurement"], "Linux /proc/self/status VmHWM")
        self.assertLess(metrics["process_peak_rss_bytes"], 320 * 1024 * 1024)
        self.assertLess(metrics["png_size_range_bytes"][1], 1024 * 1024)
