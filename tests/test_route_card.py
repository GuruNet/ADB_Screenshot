import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from route_card import find_selected_route_card


class SelectedRouteCardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'screenshot.png'
        self.image = Image.new('RGB', (360, 760), '#36393d')
        self.draw = ImageDraw.Draw(self.image)

    def card(self, x, selected):
        self.draw.rounded_rectangle((x, 532, x + 137, 638), radius=11, fill='white',
                                    outline='#0064ff' if selected else '#c9c9c9', width=2)
        self.draw.rectangle((x + 12, 553, x + 58, 558), fill='#242424')

    def locate(self):
        self.image.save(self.path)
        return find_selected_route_card(self.path)

    def test_active_card_can_be_the_second_option(self):
        self.card(5, False)
        self.card(150, True)
        self.card(295, False)
        left, top, right, bottom = self.locate()
        self.assertAlmostEqual(left, 150 / 360, delta=.007)
        self.assertAlmostEqual(right, 287 / 360, delta=.007)
        self.assertAlmostEqual(top, 532 / 760, delta=.004)
        self.assertAlmostEqual(bottom, 638 / 760, delta=.004)

    def test_two_highlights_or_missing_side_are_ambiguous(self):
        self.card(5, True)
        self.card(150, True)
        self.assertIsNone(self.locate())
        self.card(150, False)
        self.draw.rectangle((1, 547, 10, 623), fill='#36393d')
        self.assertIsNone(self.locate())

    def test_filled_actions_open_lines_and_offscreen_cards_are_ignored(self):
        self.card(-42, True)
        self.card(110, False)
        self.draw.rounded_rectangle((8, 645, 146, 701), radius=7, fill='#0064ff')
        self.draw.line([(170, 645), (300, 645), (300, 707), (170, 707)], width=3, fill='#0064ff')
        self.assertIsNone(self.locate())
