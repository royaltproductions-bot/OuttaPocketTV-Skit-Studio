"""Publisher-readiness smoke tests. No payments or external calls."""
import re
import unittest
from html.parser import HTMLParser
from urllib.parse import urlsplit
from fastapi.testclient import TestClient
import server


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.anchors = set()
        self.iframes = []
        self.headers = []
    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if data.get('id'):
            self.anchors.add(data['id'])
        if tag == 'a' and data.get('href'):
            self.links.append(data['href'])
        if tag == 'iframe':
            self.iframes.append(data)
        if tag in ('h1', 'h2', 'h3'):
            self.headers.append(tag)


class PublisherPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app)
        cls.pages = {}
        cls.html = {}
        for path in ('/', '/about', '/privacy', '/terms', '/contact'):
            response = cls.client.get(path)
            assert response.status_code == 200, (path, response.status_code)
            cls.html[path] = response.text
            parser = PageParser()
            parser.feed(response.text)
            cls.pages[path] = parser

    def test_all_publisher_pages_load_and_nav_is_not_broken(self):
        for path, parsed in self.pages.items():
            self.assertTrue(parsed.headers, path)
            self.assertIn('/about', parsed.links, path)
            self.assertIn('/privacy', parsed.links, path)
            self.assertIn('/terms', parsed.links, path)
            self.assertIn('/contact', parsed.links, path)
            self.assertIn('mobile-nav', self.html[path], path)
            self.assertTrue('content=' in self.html[path] or '<title>' in self.html[path])
            for href in parsed.links:
                if href.startswith(('https:', 'http:', 'mailto:', 'tel:')):
                    continue
                base, frag = (href.split('#', 1) + [''])[:2] if '#' in href else (href, '')
                target = base or path
                self.assertIn(target, self.pages, (path, href))
                if frag:
                    self.assertIn(frag, self.pages[target].anchors, (path, href))

    def test_real_six_video_players_without_placeholder_cards(self):
        html = self.html['/']
        parsed = self.pages['/']
        self.assertEqual(len(parsed.iframes), 6)
        ids = []
        for frame in parsed.iframes:
            self.assertIn('www.tiktok.com/player/v1/', frame.get('src', ''))
            self.assertTrue(frame.get('title'))
            self.assertIn('allowfullscreen', frame)
            ids.append(re.search(r'/player/v1/(\d+)', frame['src']).group(1))
        self.assertEqual(len(set(ids)), 6)
        # A prior scraper incorrectly assigned each video the NEXT post's caption.
        # Keep the verified post-ID-to-title mapping under regression coverage.
        expected = [
            ("7697383876368305421", "Pop-Pop’s: The First Amendment"),
            ("7700480890257820941", "Ridiculous Ideas — Join the Team"),
            ("7702540656266284302", "Trick or Treat?"),
            ("7702210754451164430", "Going Through It"),
            ("7702201665935445261", "Mental Health: Car Edition"),
            ("7700784848306261262", "The Price Is White"),
        ]
        self.assertEqual(ids, [id for id, _ in expected])
        for _, title in expected:
            self.assertIn(title, html)

        self.assertNotIn('THE THREE PINNED', html)
        self.assertNotIn('THREE NEW DROPS', html)
        self.assertLess(html.index('id="watch"'), html.index('id="studio"'))
        self.assertGreaterEqual(html.count('watch-video-meta'), 6)
        self.assertIn('MEET THE', html)

    def test_unique_editorial_and_policy_content(self):
        self.assertIn('POP-POP', self.html['/'])
        self.assertIn('BLACKGPT', self.html['/about'])
        for phrase in ('Google', 'cookies', 'TikTok', 'Stripe', 'OpenAI', 'opt out', 'privacy'):
            # Explicit opt-out links via Google My Ad Center and aboutads.
            if phrase == 'opt out':
                self.assertIn('myadcenter.google.com', self.html['/privacy'])
                continue
            self.assertIn(phrase, self.html['/privacy'] if phrase != 'BLACKGPT' else self.html['/about'])
        self.assertIn('29.99', self.html['/terms'])
        self.assertIn('mailto:outtapockettv101@gmail.com', self.html['/contact'])

    def test_ads_only_load_for_content_homepage(self):
        self.assertIn('adsbygoogle.js', self.html['/'])
        for path in ('/about', '/privacy', '/terms', '/contact'):
            self.assertNotIn('adsbygoogle.js', self.html[path], path)
            self.assertNotIn('<ins class="adsbygoogle"', self.html[path], path)
        for path in ('/order', '/admin'):
            self.assertNotIn('adsbygoogle.js', self.client.get(path).text, path)
        self.assertNotIn('optv-ad-placement', self.html['/'])

    def test_transaction_and_admin_screens_are_not_indexable(self):
        for route in ('/order', '/admin', '/api/config'):
            response = self.client.get(route)
            self.assertIn('noindex', response.headers.get('x-robots-tag', ''), route)
        for route in ('/', '/about', '/privacy', '/terms', '/contact'):
            self.assertNotIn('noindex', self.client.get(route).headers.get('x-robots-tag', ''), route)

    def test_site_discovery_routes_and_static_assets(self):
        self.assertIn('Sitemap:', self.client.get('/robots.txt').text)
        sitemap = self.client.get('/sitemap.xml')
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn('/privacy</loc>', sitemap.text)
        self.assertIn('/about</loc>', sitemap.text)
        css = self.client.get('/static/app.css')
        self.assertEqual(css.status_code, 200)
        self.assertIn('.mobile-nav', css.text)
        self.assertIn('.universe-grid', css.text)

if __name__ == '__main__':
    unittest.main()
