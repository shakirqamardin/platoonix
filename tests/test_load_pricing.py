"""Unit tests for load pricing (variable per-mile rates and £0.50 rounding)."""
import unittest

from app.services.load_pricing import compute_suggested_price_gbp, round_to_nearest_half_gbp


class TestRoundToHalf(unittest.TestCase):
    def test_examples(self) -> None:
        self.assertEqual(round_to_nearest_half_gbp(182.37), 182.5)
        self.assertEqual(round_to_nearest_half_gbp(245.23), 245.0)
        self.assertEqual(round_to_nearest_half_gbp(137.80), 138.0)
        self.assertEqual(round_to_nearest_half_gbp(245.80), 246.0)

    def test_zero(self) -> None:
        self.assertEqual(round_to_nearest_half_gbp(0), 0.0)
        self.assertEqual(round_to_nearest_half_gbp(-5), 0.0)


class TestComputeSuggestedPrice(unittest.TestCase):
    def test_van_50_curtain_normal(self) -> None:
        total, b = compute_suggested_price_gbp(50, "van", "curtain_sider", False)
        self.assertEqual(total, 75.0)
        self.assertEqual(b["suggested_gbp"], 75.0)
        self.assertEqual(b["rate_per_mile_gbp"], 1.5)

    def test_rigid_88_curtain_normal(self) -> None:
        total, b = compute_suggested_price_gbp(88, "rigid", "curtain_sider", False)
        self.assertEqual(total, 201.0)
        self.assertEqual(b["base_gbp"], 176.0)
        self.assertEqual(b["vehicle_surcharge_gbp"], 25.0)

    def test_artic_88_curtain_normal(self) -> None:
        total, b = compute_suggested_price_gbp(88, "artic", "curtain_sider", False)
        self.assertEqual(total, 292.0)
        self.assertEqual(b["base_gbp"], 242.0)
        self.assertEqual(b["vehicle_surcharge_gbp"], 50.0)

    def test_artic_88_refrigerated_urgent(self) -> None:
        total, b = compute_suggested_price_gbp(88, "artic", "refrigerated", True)
        self.assertEqual(total, 380.5)
        self.assertTrue(b["urgent"])


if __name__ == "__main__":
    unittest.main()
