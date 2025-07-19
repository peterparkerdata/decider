import asyncio
import os
from playwright.async_api import async_playwright, TimeoutError
from decider import get_recent_posts, score_profile, INVITATION_CARD_SELECTOR, human_delay

BRAVE_BROWSER_PATH = os.getenv("BRAVE_BROWSER_PATH", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")

async def find_extremists():
    profile_dir = os.path.expanduser("~/.config/browseruse/brave-profile")
    extremist_urls = []
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            executable_path=BRAVE_BROWSER_PATH,
            user_data_dir=profile_dir,
            headless=False,
        )
        page = await context.new_page()
        page.set_default_navigation_timeout(60000)
        await page.goto(
            "https://www.linkedin.com/mynetwork/invitation-manager/",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        try:
            await page.wait_for_selector(INVITATION_CARD_SELECTOR, timeout=30000)
        except TimeoutError:
            print("No invitation cards found")
            await context.close()
            return
        cards = await page.locator(INVITATION_CARD_SELECTOR).all()
        for card in cards:
            link = card.locator('a[href*="/in/"]').first
            href = await link.get_attribute("href")
            profile_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"
            new_page = await context.new_page()
            await new_page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await new_page.wait_for_timeout(5000)
            await new_page.mouse.wheel(0, 3000)
            posts = await get_recent_posts(new_page)
            score = await score_profile(new_page, posts)
            await new_page.close()
            if score < 0:
                extremist_urls.append(profile_url)
            await human_delay()
        if extremist_urls:
            print("Extremist invitations:")
            for url in extremist_urls:
                print("-", url)
        else:
            print("No extremist invitations found")
        await context.close()

if __name__ == "__main__":
    asyncio.run(find_extremists())
