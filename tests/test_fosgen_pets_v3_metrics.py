"""Independent pixel fixtures for the offline visual diagnostic metrics."""
from importlib import import_module, util
from io import BytesIO
import math
import unittest

from PIL import Image, ImageDraw


def png(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class VisualMetricsTests(unittest.TestCase):
    def metrics(self):
        self.assertIsNotNone(util.find_spec("fosgen_pets.visual_metrics"),
                             "the v3 review needs real raster comparisons")
        return import_module("fosgen_pets.visual_metrics")

    def test_duplicate_rgb_pixels_are_identified_even_if_png_metadata_differs(self):
        from PIL.PngImagePlugin import PngInfo
        image = Image.new("RGB", (64, 64), "#ff8040")
        first = png(image)
        metadata = PngInfo()
        metadata.add_text("review", "same pixels, another encoding")
        output = BytesIO()
        image.save(output, format="PNG", pnginfo=metadata)
        result = self.metrics().compare_images(first, output.getvalue())
        self.assertTrue(result["identical_rgb"])
        self.assertEqual(result["phash_distance"], 0)
        self.assertEqual(result["ssim"], 1.0)
        self.assertEqual(result["changed_pixel_ratio"], 0.0)
        self.assertEqual(result["mean_absolute_rgb_difference"], 0.0)

    def test_black_white_comparison_has_hand_derived_luminance_and_color_distance(self):
        result = self.metrics().compare_images(
            png(Image.new("RGB", (64, 64), "black")),
            png(Image.new("RGB", (64, 64), "white")),
        )
        self.assertFalse(result["identical_rgb"])
        self.assertEqual(result["changed_pixel_ratio"], 1.0)
        self.assertEqual(result["mean_absolute_rgb_difference"], 255.0)
        # Constants have zero local variance: SSIM reduces to C1/(255^2+C1).
        self.assertAlmostEqual(result["ssim"], 0.0000999900009999, places=10)
        self.assertEqual(result["phash_distance"], 0)

    def test_phash_detects_large_structural_rearrangement(self):
        first = Image.new("RGB", (128, 128), "white")
        second = first.copy()
        ImageDraw.Draw(first).rectangle((8, 8, 60, 100), fill="black")
        ImageDraw.Draw(second).ellipse((70, 30, 124, 124), fill="black")
        result = self.metrics().compare_images(png(first), png(second))
        self.assertGreater(result["phash_distance"], 12)
        self.assertLess(result["ssim"], 0.7)

    def test_rgb_comparison_catches_recoloring_that_structural_hash_misses(self):
        result = self.metrics().compare_images(
            png(Image.new("RGB", (64, 64), "red")),
            png(Image.new("RGB", (64, 64), "blue")),
        )
        self.assertEqual(result["phash_distance"], 0)
        self.assertFalse(result["identical_rgb"])
        self.assertEqual(result["mean_absolute_rgb_difference"], 170.0)
        self.assertEqual(result["changed_pixel_ratio"], 1.0)

    def test_foreground_summary_crops_transparency_and_reports_real_contrast(self):
        image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((24, 16, 71, 79), fill=(230, 230, 230, 255))
        drawing.rectangle((24, 16, 47, 79), fill=(20, 20, 20, 255))
        result = self.metrics().image_metrics(png(image))
        self.assertEqual((result["width"], result["height"]), (96, 96))
        self.assertEqual(result["foreground_bbox"], [24, 16, 72, 80])
        self.assertAlmostEqual(result["foreground_luminance_contrast"], 210.0)
        self.assertAlmostEqual(sum(result["foreground_luminance_histogram"]), 1.0)
        self.assertEqual(len(result["phash"]), 16)
        self.assertEqual(len(result["rgb_sha256"]), 64)

    def test_solid_similarity_is_diagnostic_and_patterns_have_stronger_review_thresholds(self):
        first = Image.new("RGB", (64, 64), "#dcb590")
        second = first.copy()
        ImageDraw.Draw(second).rectangle((30, 30, 31, 31), fill="#d0aa88")
        values = [self.metrics().compare_images(png(first), png(second),
                                               species="kitten", pattern=pattern)
                  for pattern in ("solid", "tabby")]
        self.assertEqual(values[0]["diagnostic"]["review_level"], "info")
        self.assertEqual(values[1]["diagnostic"]["review_level"], "review")
        self.assertGreater(values[1]["diagnostic"]["minimum_changed_pixel_ratio"],
                           values[0]["diagnostic"]["minimum_changed_pixel_ratio"])
        self.assertNotIn("unique", values[0])

    def test_different_dimensions_are_explicitly_rejected(self):
        with self.assertRaisesRegex(ValueError, "dimensions"):
            self.metrics().compare_images(png(Image.new("RGB", (32, 32))),
                                          png(Image.new("RGB", (64, 64))))

    def test_finite_metrics_and_untrusted_formats_have_bounded_input(self):
        metrics = self.metrics()
        result = metrics.image_metrics(png(Image.new("RGB", (32, 32), "white")))
        for value in result.values():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value))
        for invalid in (b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"invalid"):
            with self.assertRaises(ValueError):
                metrics.image_metrics(invalid)
        with self.assertRaises(TypeError):
            metrics.image_metrics("/tmp/not-a-png.png")


if __name__ == "__main__":
    unittest.main()
