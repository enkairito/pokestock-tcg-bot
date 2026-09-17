import unittest

from gaming_product_filter import is_non_gaming_product


class GamingProductFilterTests(unittest.TestCase):
    def test_rejects_observed_books_films_and_music(self):
        rejected = (
            ("1401263119", "Batman: The Dark Knight Returns 30th Anniversary Edition"),
            ("1684058414", "Teenage Mutant Ninja Turtles: The Last Ronin"),
            ("0571226167", "The Bell Jar: Sylvia Plath"),
            ("B07HGBVPSK", "Johnny Hallyday : 1943-2017 au coeur de la légende [DVD]"),
            ("B00WVQQGZE", "Minecraft Volume Alpha"),
            ("B0CKWKDPR1", "Where the Buffalo Roam [Blu-ray]"),
        )
        for asin, name in rejected:
            with self.subTest(asin=asin):
                self.assertTrue(is_non_gaming_product(asin, name))

    def test_keeps_games_and_consoles_with_physical_media_words(self):
        accepted = (
            ("B0G1234567", "Metal Gear Solid: Master Collection Volume 2 – Day One Edition - PS5"),
            ("B09HD6JK6P", "Disco Elysium - The Final Cut (Xbox One) [Blu-ray]"),
            ("B0CQPMYTVM", "SONY PS5 with Blu-Ray 1TB SSD D-Chassis Slim EU"),
            ("B0F2J4SYJ2", "Nintendo Switch 2"),
            ("B000000001", "Stardew Valley"),
        )
        for asin, name in accepted:
            with self.subTest(asin=asin):
                self.assertFalse(is_non_gaming_product(asin, name))


if __name__ == "__main__":
    unittest.main()
