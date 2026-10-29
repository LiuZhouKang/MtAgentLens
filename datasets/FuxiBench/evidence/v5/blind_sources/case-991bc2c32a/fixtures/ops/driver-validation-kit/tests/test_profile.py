import unittest

from profile import classify_load, moving_average, parse_cpu_model, parse_uptime


class TestHostFacts(unittest.TestCase):
    def test_parse_cpu_model_reads_the_first_model_line(self):
        text = (
            "processor\t: 0\n"
            "model name\t: Example CPU 7000\n"
            "cpu MHz\t\t: 3200.000\n"
            "model name\t: Example CPU 7000 (duplicate)\n"
        )
        self.assertEqual(parse_cpu_model(text), "Example CPU 7000")

    def test_parse_cpu_model_returns_unknown_without_the_field(self):
        self.assertEqual(parse_cpu_model("processor\t: 0\n"), "unknown")
        self.assertEqual(parse_cpu_model(""), "unknown")

    def test_parse_uptime_reads_the_first_field_in_seconds(self):
        self.assertEqual(parse_uptime("12345.6 98765.4\n"), 12345.6)
        self.assertEqual(parse_uptime(""), 0.0)
        self.assertEqual(parse_uptime("not-a-number\n"), 0.0)


class TestLoadSummary(unittest.TestCase):
    def test_moving_average_warms_up_with_partial_windows(self):
        self.assertEqual(moving_average([10.0, 20.0, 30.0], 2), [10.0, 15.0, 25.0])

    def test_moving_average_rejects_a_non_positive_window(self):
        with self.assertRaises(ValueError):
            moving_average([1.0], 0)

    def test_moving_average_of_no_samples_is_empty(self):
        self.assertEqual(moving_average([], 3), [])

    def test_classify_load_buckets_by_iterations_per_second(self):
        self.assertEqual(classify_load(100, 5.0), "idle")
        self.assertEqual(classify_load(50_000, 5.0), "light")
        self.assertEqual(classify_load(2_000_000, 5.0), "saturated")
        self.assertEqual(classify_load(10, 0.0), "unknown")


if __name__ == "__main__":
    unittest.main()
