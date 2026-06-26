import asyncio

from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)
        print("playwright_ok", await page.title(), page.url)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
