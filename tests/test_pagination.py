import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from acquire_metadata import check_next_url


class PaginationIdentity(unittest.TestCase):
    def test_named_repository_link(self):
        check_next_url(
            "https://api.github.com/repos/Example/project/releases?per_page=100&page=2",
            "Example/project",
            123,
            2,
        )

    def test_verified_numeric_repository_link(self):
        check_next_url(
            "https://api.github.com/repositories/123/releases?per_page=100&page=2",
            "Example/project",
            123,
            2,
        )

    def test_other_repository_id_rejected(self):
        with self.assertRaises(AssertionError):
            check_next_url(
                "https://api.github.com/repositories/999/releases?per_page=100&page=2",
                "Example/project",
                123,
                2,
            )

    def test_offsite_and_userinfo_rejected(self):
        for url in [
            "https://example.com/repositories/123/releases?per_page=100&page=2",
            "https://user@api.github.com/repositories/123/releases?per_page=100&page=2",
        ]:
            with self.assertRaises(AssertionError):
                check_next_url(url, "Example/project", 123, 2)

    def test_skipped_page_rejected(self):
        with self.assertRaises(AssertionError):
            check_next_url(
                "https://api.github.com/repositories/123/releases?per_page=100&page=3",
                "Example/project",
                123,
                2,
            )

    def test_extra_query_or_fragment_rejected(self):
        for suffix in ["&token=INVALID_EXAMPLE_VALUE", "#ignored"]:
            with self.assertRaises(AssertionError):
                check_next_url(
                    "https://api.github.com/repositories/123/releases?per_page=100&page=2"
                    + suffix,
                    "Example/project",
                    123,
                    2,
                )


if __name__ == "__main__":
    unittest.main()
