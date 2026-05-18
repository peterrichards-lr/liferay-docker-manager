import os

import pytest
from playwright.sync_api import Page, expect


@pytest.fixture(scope="module")
def liferay_url():
    return os.environ.get("LIFERAY_URL", "http://localhost:8082")


def test_fragment_deployment(page: Page, liferay_url: str):
    """
    Verifies that the 'Test Collection' fragment collection and its 'Test Fragment'
    are correctly deployed and visible in the Liferay UI.
    """
    print(f"Connecting to Liferay at {liferay_url}...")

    # 1. Login
    page.goto(f"{liferay_url}/c/portal/login")

    # Check if we are already logged in or if login page appeared
    login_field = page.locator('input[name*="LoginPortlet_login"]')
    if login_field.is_visible(timeout=10000):
        page.fill('input[name*="LoginPortlet_login"]', "test@liferay.com")
        page.fill('input[name*="LoginPortlet_password"]', "test")
        page.click('button[type="submit"]')

    # Wait for the home page to load or check if we're at home
    try:
        page.wait_for_url(f"{liferay_url}/web/guest**", timeout=60000)
    except Exception:
        # Fallback: maybe we are already at home or redirected elsewhere
        print(f"Current URL after login: {page.url}")

    # 2. Navigate to Fragments
    # We use the direct portlet URL for efficiency
    fragments_url = f"{liferay_url}/group/control_panel/manage?p_p_id=com_liferay_fragment_web_portlet_FragmentPortlet"
    page.goto(fragments_url)

    # 3. Verify Collection exists
    # Using locator with text to be more precise in Liferay's complex UI
    collection_item = page.locator(".clay-card", has_text="Test Collection")

    # If card layout isn't used, try list layout
    if not collection_item.is_visible(timeout=2000):
        collection_item = page.locator("tr", has_text="Test Collection")

    # Final fallback to generic text
    if not collection_item.is_visible(timeout=2000):
        collection_item = page.get_by_text("Test Collection")

    expect(collection_item.first).to_be_visible(timeout=30000)
    print("Found 'Test Collection'. Checking fragments...")

    # 4. Click the collection and verify fragment
    collection_item.first.click()

    fragment_item = page.get_by_text("Test Fragment")
    expect(fragment_item.first).to_be_visible(timeout=10000)

    print("✅ Fragment 'Test Fragment' found in 'Test Collection'.")
    print("✅ Fragment deployment verified via UI.")


if __name__ == "__main__":
    # Allow running directly via python if needed
    pytest.main([__file__])
