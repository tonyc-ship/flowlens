import unittest

from flowlens.core.process_metrics import parse_size_to_mb


class ProcessMetricsTest(unittest.TestCase):
    def test_parse_size_to_mb_handles_top_units(self) -> None:
        self.assertEqual(parse_size_to_mb("8557M"), 8557.0)
        self.assertEqual(parse_size_to_mb("8.37G"), 8570.88)
        self.assertEqual(parse_size_to_mb("1024K"), 1.0)


if __name__ == "__main__":
    unittest.main()
