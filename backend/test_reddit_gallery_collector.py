import importlib.util
import unittest
from pathlib import Path


PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "playwright is not installed")
class RedditGalleryCollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        chrome_path = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        try:
            cls.browser = cls.playwright.chromium.launch(
                headless=True,
                executable_path=str(chrome_path) if chrome_path.is_file() else None,
            )
        except Exception as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium is unavailable: {exc}") from exc

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def test_collects_loaded_and_lazy_gallery_foreground_images(self) -> None:
        from app.reddit_detail_collector import PostDetailObservationCollector

        page = self.browser.new_page()
        page.route("**/*", lambda route: route.abort())
        gallery_pages = []
        expected_urls = []
        for index in range(1, 11):
            image_url = f"https://preview.redd.it/gallery-v0-image{index}.jpg?width=640"
            expected_urls.append(image_url)
            source_attribute = "src" if index <= 4 else "data-lazy-src"
            gallery_pages.append(
                f"""
                <li slot="page-{index}">
                  <img role="presentation" src="https://preview.redd.it/background-{index}.jpg" />
                  <figure>
                    <img class="media-lightbox-img" {source_attribute}="{image_url}" />
                  </figure>
                </li>
                """
            )
        page.set_content(
            f"""
            <shreddit-post
              post-type="gallery"
              post-title="Nanshan district"
              content-href="https://www.reddit.com/gallery/example"
              subreddit-prefixed-name="r/shenzhen"
            >
              <div slot="post-media-container">
                <gallery-carousel><ul>{''.join(gallery_pages)}</ul></gallery-carousel>
              </div>
            </shreddit-post>
            """
        )

        observation = PostDetailObservationCollector().collect(page, origin="test")

        self.assertEqual(observation.post_type, "gallery")
        self.assertEqual(observation.media_urls, expected_urls)
        self.assertFalse(any("background" in url for url in observation.media_urls))
        page.close()


if __name__ == "__main__":
    unittest.main()
