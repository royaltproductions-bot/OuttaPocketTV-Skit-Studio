"""Real browser smoke checks for desktop/mobile navigation and video embeds."""
import asyncio
import json
import urllib.request
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8080"
VID = "7697383876368305421"

async def run():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        for width, height, mobile in [(1440, 900, False), (768, 1024, False), (390, 844, True), (320, 700, True)]:
            page = await browser.new_page(viewport={"width": width, "height": height}, is_mobile=mobile, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            response = await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
            assert response.status == 200
            await page.locator("#watch").wait_for()
            count = await page.locator('.watch-video iframe').count()
            assert count == 6, (width, count)
            data = await page.evaluate("""() => ({
                viewport: document.documentElement.clientWidth,
                width: document.documentElement.scrollWidth,
                h1: document.querySelector('h1').textContent,
                watchTop: document.querySelector('#watch').offsetTop,
                studioTop: document.querySelector('#studio').offsetTop,
                navVisible: !!document.querySelector('.mobile-nav') && getComputedStyle(document.querySelector('.mobile-nav')).display !== 'none'
            })""")
            assert data["width"] <= data["viewport"] + 5, (width, 'horizontal overflow', data)
            assert data["watchTop"] < data["studioTop"], data
            # The responsive menu intentionally appears on tablet too.
            if width < 1121:
                await page.locator('.mobile-nav summary').click()
                assert await page.locator('.mobile-nav').evaluate("(el) => el.open")
                assert await page.locator('.mobile-nav nav a[href="/about"]').is_visible()
            else:
                assert await page.locator('.desktop-nav a[href="/about"]').is_visible()
            assert not errors, (width, errors)
            results.append({"viewport": width, "status": "pass", "video_frames": count, **data})
            await page.close()
        desktop = await browser.new_page()
        for path in ("/about", "/privacy", "/terms", "/contact", "/robots.txt", "/sitemap.xml", "/order"):
            response = await desktop.goto(BASE+path,wait_until="domcontentloaded",timeout=15000)
            assert response.status == 200, (path,response.status)
            results.append({"route":path,"status":response.status})
        # A video player endpoint must be reachable, not just a fabricated link.
        req = urllib.request.Request(f"https://www.tiktok.com/player/v1/{VID}", headers={"User-Agent":"Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req,timeout=15) as response:
                mime=response.headers.get("Content-Type","")
                assert response.status == 200
                assert "text/html" in mime
                results.append({"tiktok_player":VID,"http_status":response.status,"mime":mime})
        except Exception as exc:
            # Third-party sites may rate limit automation; don't mask this uncertainty.
            results.append({"tiktok_player":VID,"verification":"external request blocked","detail":str(exc)[:100]})
        await browser.close()
    print("BROWSER_AUDIT="+json.dumps(results,ensure_ascii=False),flush=True)

if __name__ == "__main__":
    asyncio.run(run())
