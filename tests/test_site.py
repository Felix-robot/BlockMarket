import json
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from blockmarket.verifier import verify_replay


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if tag == "link" and values.get("href"):
            self.assets.append(values["href"] or "")
        if tag == "script" and values.get("src"):
            self.assets.append(values["src"] or "")


class StaticSiteTests(unittest.TestCase):
    def test_required_site_files_exist(self) -> None:
        for relative in (
            "index.html",
            "styles.css",
            "app.js",
            "favicon.svg",
            ".nojekyll",
            "data/demo-replay.json",
        ):
            self.assertTrue((SITE / relative).is_file(), relative)

    def test_html_assets_are_project_pages_safe(self) -> None:
        parser = _AssetParser()
        parser.feed((SITE / "index.html").read_text(encoding="utf-8"))
        for asset in parser.assets:
            parsed = urlparse(asset)
            self.assertFalse(parsed.path.startswith("/"), asset)
            if not parsed.scheme:
                target = SITE / parsed.path.removeprefix("./")
                self.assertTrue(target.is_file(), asset)
        self.assertEqual(len(parser.ids), len(set(parser.ids)), "duplicate HTML id")

    def test_demo_replay_is_current_and_independently_verifiable(self) -> None:
        replay = json.loads((SITE / "data/demo-replay.json").read_text(encoding="utf-8"))
        self.assertEqual(replay["manifest"]["ruleset"], "blockmarket-v1-prototype.2")
        self.assertEqual(replay["summary"]["blocks_completed"], 48)
        result = verify_replay(replay)
        self.assertTrue(result["valid"])
        self.assertEqual(result["events_verified"], 48)

    def test_site_is_a_fixed_three_screen_deck(self) -> None:
        html = (SITE / "index.html").read_text(encoding="utf-8")
        css = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertEqual(html.count(' data-slide="'), 3)
        self.assertIn("html, body { width: 100%; height: 100%; overflow: hidden; }", css)
        self.assertIn('id="deck-prev"', html)
        self.assertIn('id="deck-next"', html)

    def test_public_site_links_to_source_and_player_guide(self) -> None:
        html = (SITE / "index.html").read_text(encoding="utf-8")
        self.assertIn("https://github.com/Felix-robot/BlockMarket", html)
        self.assertIn("https://github.com/Felix-robot/BlockMarket-Bots", html)
        self.assertIn("提交只要 3 步", html)
        self.assertIn("现在提交 Bot", html)
        self.assertIn("点 Fork", html)
        self.assertIn("发 Pull Request", html)
        self.assertIn("docs/PLAYER_GUIDE.md", html)
        self.assertIn("src/blockmarket/bots.py#L71-L86", html)
        self.assertIn("src/blockmarket/bots.py#L46-L54", html)
        self.assertIn("src/blockmarket/orderflow.py", html)
        self.assertIn("docs/RULES_V1.md", html)
        self.assertIn("查看 A Bot 源码", html)
        self.assertIn("查看 B Bot 源码", html)
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)

        player_guide = (ROOT / "docs" / "PLAYER_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("提交只要 3 步", player_guide)
        self.assertIn("https://github.com/Felix-robot/BlockMarket-Bots", player_guide)
        self.assertIn("Pull Request", player_guide)

    def test_large_type_overrides_cover_key_small_copy(self) -> None:
        css = (SITE / "styles.css").read_text(encoding="utf-8")
        self.assertIn("Large-type pass", css)
        self.assertIn(".bot-source-links a { font-size: 17px; }", css)
        self.assertIn(".evidence-card > p { font-size: 18px; }", css)
        self.assertIn(".market-formula { font-size: 16px; }", css)
        self.assertIn(".lead { font-size: 20px; }", css)


if __name__ == "__main__":
    unittest.main()
