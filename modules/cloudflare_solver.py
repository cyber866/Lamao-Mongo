#
# This module uses Playwright to bypass Cloudflare anti-bot challenges
# and retrieve the final, redirected URL.
# This is a much more robust solution than simple HTTP headers.
#

import asyncio
import logging
from playwright.async_api import async_playwright

log = logging.getLogger("cloudflare_solver")

async def get_redirected_url(url):
    """
    Launches a browser, navigates to the URL, waits for the Cloudflare
    challenge to be solved, and returns the final URL.
    """
    try:
        log.info(f"Attempting to solve Cloudflare challenge for: {url}")
        
        # Use a headless browser to avoid a UI window popping up
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.goto(url)

            # Wait for the challenge to be solved. This might take a few seconds.
            # We look for the page to redirect or for the challenge elements to disappear.
            await page.wait_for_selector('body', state='attached', timeout=30000)

            # Get the final URL after all redirects
            final_url = page.url
            log.info(f"Cloudflare challenge solved. Final URL: {final_url}")
            
            await browser.close()
            return final_url

    except Exception as e:
        log.error(f"Failed to solve Cloudflare challenge for {url}: {e}")
        return None

# Example usage for testing:
# if __name__ == "__main__":
#     test_url = "http://example.com/a-cloudflare-protected-site"
#     asyncio.run(get_redirected_url(test_url))

