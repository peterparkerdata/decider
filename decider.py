import logging
import asyncio
import random
import os
import datetime
from openai import AsyncOpenAI
from playwright.async_api import async_playwright, TimeoutError

# Enable verbose debug logging so execution progress is visible in the console
logging.basicConfig(level=logging.DEBUG, format="%(message)s")

# --- Configuration ---
BRAVE_BROWSER_PATH = os.getenv(
    "BRAVE_BROWSER_PATH",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY environment variable not set.")
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

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
    """Return True if any keyword is found in the text."""
    return any(kw.lower() in (text or "").lower() for kw in keywords)

async def get_recent_posts(page) -> list[str]:
    """Return up to five recent post texts from the profile page."""
    logging.info(f"Fetching recent posts from {page.url}")
    posts = []
    valid_count = 0
    try:
        await page.goto(page.url + "recent-activity/all/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        elems = await page.locator("div.feed-shared-update-v2").all()
        for el in elems[:5]:
            txt = (await el.inner_text()).strip()
            logging.debug(f"Post snippet: {txt[:60]!r}")
            if txt:
                posts.append(txt)
                valid_count += 1
    except Exception as e:
        logging.warning(f"get_recent_posts error: {e}")
    logging.info(f"Collected {valid_count} valid posts")
    return posts

async def human_delay(min_sec=3, max_sec=7):
    """Sleep for a random human-like delay."""
    d = random.uniform(min_sec, max_sec)
    if random.random() < 0.1:
        d = random.uniform(10, 20)
    logging.debug(f"Sleeping for {d:.1f}s…")
    await asyncio.sleep(d)
    await asyncio.sleep(random.uniform(0.2, 0.8))

async def classify_extremism(posts: list[str]) -> float:
    """Return an extremism risk score between 0.0 and 1.0."""
    logging.info("Classifying extremism risk for posts")

    # Require sufficient post text before calling the language model
    joined = "\n".join(p.strip() for p in posts if p.strip())
    valid_count = sum(1 for p in posts if p.strip())
    logging.debug(f"Total characters in posts: {len(joined)}, valid post count: {valid_count}")
    if joined:
        logging.debug(f"Post sample for classification: {joined[:80]!r}")
    if len(joined) < 100:
        logging.info(f"Not enough post content to evaluate extremism (len={len(joined)})")
        return 0.0

    prompt = (
        f"Evaluate the following LinkedIn posts for extremist/divisive content across: {RISK_TAXONOMY}. "
        "Rate overall risk 0.0–1.0, reply with a single number.\n\nPosts:\n" + "\n---\n".join(posts)
    )
    try:
        resp = await client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        score = float(resp.choices[0].message.content.strip())
        logging.info(f"Extremism risk score: {score}")
        return score
    except Exception as e:
        logging.warning(f"classify_extremism parse failed: {e}")
        return 0.0

async def score_profile(page, posts: list[str]) -> int:
    """Score a LinkedIn profile based on its text, connections, and posts."""
    logging.info(f"Scoring profile {page.url}")
    valid_posts = [p for p in posts if p.strip()]
    logging.debug(f"Non-empty posts count: {len(valid_posts)}")
    for sample in valid_posts[:3]:
        logging.debug(f"Post sample in scoring: {sample[:60]!r}")
    text = ""
    selectors = [
        'section:has(h2:has-text("About")) .inline-show-more-text',
        'section:has(h2:has-text("About"))',
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
    logging.debug(f"Profile text snippet: {text[:80]!r}")

    conn = None
    try:
        await page.wait_for_selector('span:has-text("connections")', timeout=5000)
        conn_txt = await page.locator('span:has-text("connections")').first.inner_text()
        logging.debug(f"Raw connection text: {conn_txt!r}")
        digits = ''.join(c for c in conn_txt if c.isdigit())
        conn = int(digits)
    except Exception:
        pass
    logging.debug(f"Connection count: {conn}")

    if contains_any(text, KEYWORDS_BLOCK) or any(contains_any(p, KEYWORDS_BLOCK) for p in posts):
        return -10

    # Only evaluate extremism if we have ample content
    long_posts = [p for p in posts if len(p.strip()) >= 40]
    logging.debug(f"Posts meeting length threshold: {len(long_posts)}")
    if len(text) >= 100 or long_posts:
        risk = await classify_extremism(posts)
        if risk >= 0.7:
            return -10
    else:
        logging.info("Insufficient content for extremism check")
        risk = 0.0

    score = 0
    if contains_any(text, INSTITUTION_KEYWORDS):
        score += 3
    if contains_any(text, MITPE_COURSE_KEYWORDS):
        score += 2
    if conn and conn >= MIN_CONNECTIONS:
        score += 1
    logging.info(f"Computed score {score}")
    return score

async def process_invitations(context):
    """Process pending invitations using the provided browser context."""
    logging.info("Starting invitation processing")
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
        logging.info("No invitation cards found")
        await context.close()
        return

    cards_locator = page.locator(INVITATION_CARD_SELECTOR)
    count = await cards_locator.count()
    logging.info(f"Found {count} invitation cards")
    await page.wait_for_timeout(PAGE_WAIT * 1000)

    processed = 0
    while processed < MAX_INVITATIONS:
        cards = await cards_locator.all()
        if not cards:
            break

        for card in cards:
            if processed >= MAX_INVITATIONS:
                break

            logging.info(f"Processing invitation {processed + 1}")
            link = card.locator('a[href*="/in/"]').first
            href = await link.get_attribute("href")
            profile_url = href if href.startswith("http") else f"https://www.linkedin.com{href}"
            logging.info(f"Visiting profile {profile_url}")
            new_pg = await context.new_page()
            await new_pg.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await new_pg.wait_for_timeout(5000)
            await new_pg.mouse.wheel(0, 3000)
            posts = await get_recent_posts(new_pg)
            score = await score_profile(new_pg, posts)
            await new_pg.close()
            await human_delay()

            accept_btn = card.locator('button:has-text("Accept")')
            if score >= 0 and await accept_btn.count():
                await accept_btn.first.click()
                logging.info(f"Accepted invitation from {profile_url}")
            else:
                with open("rejected.txt", "a") as f:
                    f.write(profile_url + "\n")
                logging.info(f"Rejected invitation from {profile_url} with score {score}")
            processed += 1
            await human_delay()

        if processed < MAX_INVITATIONS:
            logging.info("Reloading invitation manager page")
            await page.reload(wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(PAGE_WAIT * 1000)
    await context.close()
    logging.info("Finished processing invitations")

async def main():
    """Entry point for running the invitation processor."""
    now = datetime.datetime.now()
    if not (9 <= now.hour < 23):
        logging.info("Outside active hours, exiting")
        return

    profile_dir = os.path.expanduser("~/.config/browseruse/brave-profile")
    async with async_playwright() as p:
        logging.info("Launching browser context")
        context = await p.chromium.launch_persistent_context(
            executable_path=BRAVE_BROWSER_PATH,
            user_data_dir=profile_dir,
            headless=False,
        )
        await process_invitations(context)

if __name__ == "__main__":
    asyncio.run(main())
