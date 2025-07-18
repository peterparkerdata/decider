import asyncio
import random
import time
import os
import datetime
from openai import OpenAI
from playwright.async_api import async_playwright, TimeoutError

# — Configuration —
OPENAI_API_KEY = "OpenAI API Key"  
client = OpenAI(api_key=OPENAI_API_KEY)

INVITATION_CARD_SELECTOR = 'div[data-view-name="pending-invitation"] div[role="listitem"]'
KEYWORDS_BLOCK = ["antifa", "blood and soil", "globalist cabal", "white supremacist", "boogaloo"]
RISK_TAXONOMY = (
    "Abortion (pro-life vs pro-choice), Gun control, Immigration policy, "
    "Climate change denial vs activism, Vaccination debates, Critical Race Theory, "
    "LGBTQ+ rights, BLM vs All Lives Matter, Police reform, Religious fundamentalism, "
    "Atheism vs religion debates, Free speech vs cancel culture, Socialism vs capitalism, "
    "Healthcare models, Tax policy debates, Conspiracy theories, Genocide denial, "
    "Extremist ideologies"
)
INSTITUTION_KEYWORDS = ["MIT Professional Education", "MIT Professional", "MIT", "Harvard"]
MITPE_COURSE_KEYWORDS = ["Generative AI", "Architecting and Engineering Software Systems for Generative AI"]
MAX_INVITATIONS = 40
PAGE_WAIT = 5
MIN_CONNECTIONS = 50


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(kw.lower() in (text or "").lower() for kw in keywords)


async def get_recent_posts(page) -> list[str]:
    posts = []
    try:
        await page.goto(page.url + "recent-activity/all/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        elems = await page.locator("div.feed-shared-update-v2").all()
        for el in elems[:5]:
            txt = (await el.inner_text()).strip()
            posts.append(txt)
    except Exception as e:
        print(f"[warn] get_recent_posts error: {e}")
    return posts


def human_delay(min_sec=3, max_sec=7):
    d = random.uniform(min_sec, max_sec)
    if random.random() < 0.1:
        d = random.uniform(10, 20)
    print(f"[debug] Sleeping for {d:.1f}s…")
    time.sleep(d)
    time.sleep(random.uniform(0.2, 0.8))


async def classify_extremism(posts: list[str]) -> float:
    prompt = (
        f"Evaluate the following LinkedIn posts for extremist/divisive content across: {RISK_TAXONOMY}. "
        "Rate overall risk 0.0–1.0, reply with a single number.\n\nPosts:\n"
        + "\n---\n".join(posts)
    )
    resp = await asyncio.to_thread(
        client.chat.completions.create,
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    try:
        return float(resp.choices[0].message.content.strip())
    except Exception as e:
        print(f"[warn] classify_extremism parse failed: {e}")
        return 0.0


async def score_profile(page, posts: list[str]) -> int:
    text = ""
    selectors = [
        "section.pv-about-section",
        ".pv-about__summary-text",
        ".pv-top-card--list-bullet",
    ]
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=5000)
            el = page.locator(sel).first
            text = await el.inner_text()
            if text:
                break
        except Exception:
            continue
    print(f"[debug] Profile text snippet: {text[:80]!r}")

    conn = None
    try:
        await page.wait_for_selector('span:has-text("connections")', timeout=5000)
        conn_txt = await page.locator('span:has-text("connections")').first.inner_text()
        print(f"[debug] Raw connection text: {conn_txt!r}")
        digits = ''.join(c for c in conn_txt if c.isdigit())
        conn = int(digits)
    except Exception:
        pass
    print(f"[debug] Connection count: {conn}")

    if contains_any(text, KEYWORDS_BLOCK) or any(contains_any(p, KEYWORDS_BLOCK) for p in posts):
        return -10
    if conn is not None and conn < MIN_CONNECTIONS and not contains_any(text, INSTITUTION_KEYWORDS):
        return -10

    risk = await classify_extremism(posts)
    if risk >= 0.7:
        return -10

    score = 0
    if contains_any(text, INSTITUTION_KEYWORDS):
        score += 3
    if contains_any(text, MITPE_COURSE_KEYWORDS):
        score += 2
    if conn and conn >= MIN_CONNECTIONS:
        score += 1
    return score


async def process_invitations(context):
    page = await context.new_page()
    page.set_default_navigation_timeout(60000)
    await page.goto(
        "https://www.linkedin.com/mynetwork/invitation-manager/",
        wait_until="domcontentloaded",
        timeout=60000
    )

    try:
        await page.wait_for_selector(INVITATION_CARD_SELECTOR, timeout=30000)
    except TimeoutError:
        print("No invitation cards found")
        await context.close()
        return

    cards_locator = page.locator(INVITATION_CARD_SELECTOR)
    await page.wait_for_timeout(PAGE_WAIT * 1000)

    processed = 0
    while processed < MAX_INVITATIONS:
        cards = await cards_locator.all()
        if not cards:
            break

        for card in cards:
            if processed >= MAX_INVITATIONS:
                break

            link = card.locator('a[href*="/in/"]').first
            href = await link.get_attribute("href")
            profile_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"
            new_pg = await context.new_page()
            await new_pg.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await new_pg.wait_for_timeout(5000)
            await new_pg.mouse.wheel(0, 3000)
            posts = await get_recent_posts(new_pg)
            score = await score_profile(new_pg, posts)
            await new_pg.close()
            human_delay()

            accept_btn = card.locator('button:has-text("Accept")')
            if score >= 3 and await accept_btn.count():
                await accept_btn.first.click()
            else:
                with open("rejected.txt", "a") as f:
                    f.write(profile_url + "\n")
            processed += 1
            human_delay()

        if processed < MAX_INVITATIONS:
            await page.reload(wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(PAGE_WAIT * 1000)
    await context.close()


async def main():
    now = datetime.datetime.now()
    if not (9 <= now.hour < 23):
        return

    profile_dir = os.path.expanduser("~/.config/browseruse/brave-profile")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            executable_path="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            user_data_dir=profile_dir,
            headless=False,
        )
        await process_invitations(context)


if __name__ == "__main__":
    asyncio.run(main())
