import unittest

from pipeline import ewma, parse_stage_line, throughput_ok


class TestStageTiming(unittest.TestCase):
    def test_parse_stage_line_reads_name_and_milliseconds(self):
        self.assertEqual(parse_stage_line("ingest_batch=12.5"), ("ingest_batch", 12.5))

    def test_parse_stage_line_skips_blank_comment_and_malformed(self):
        self.assertIsNone(parse_stage_line("   "))
        self.assertIsNone(parse_stage_line("# window=maintenance"))
        self.assertIsNone(parse_stage_line("ingest_batch=slow"))
        self.assertIsNone(parse_stage_line("=10"))

    def test_ewma_seeds_with_first_sample(self):
        self.assertEqual(ewma([], 0.5), 0.0)
        self.assertEqual(ewma([7.0], 0.5), 7.0)

    def test_ewma_weights_the_recent_sample(self):
        self.assertAlmostEqual(ewma([10.0, 20.0], 0.5), 15.0)
        self.assertAlmostEqual(ewma([10.0, 20.0, 30.0], 0.5), 22.5)

    def test_throughput_ok_requires_every_sample_above_floor(self):
        self.assertTrue(throughput_ok([9, 10, 11], 5))
        self.assertFalse(throughput_ok([1, 2], 5))
        self.assertFalse(throughput_ok([], 5))


if __name__ == "__main__":
    unittest.main()
