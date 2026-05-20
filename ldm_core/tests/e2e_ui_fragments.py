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
    # Optimize E2E tests by blocking external tracking and status scripts
    page.route("**/*.statuspage.io/**", lambda route: route.abort())
    page.route("**/cdn.pendo.io/**", lambda route: route.abort())

    print(f"Connecting to Liferay at {liferay_url}...")

    # 1. Login
    page.goto(f"{liferay_url}/c/portal/login")

    # Check if we are already logged in or if login page appeared
    login_field = page.locator('input[name*="LoginPortlet_login"]')
    if login_field.is_visible(timeout=10000):
        page.fill('input[name*="LoginPortlet_login"]', "test@liferay.com")
        page.fill('input[name*="LoginPortlet_password"]', "test")
        page.click('button[type="submit"]')

    # LDM-385: Handle potential license activation blocks in CI
    if "license_activation" in page.url:
        print("⚠️  Redirected to license activation page. Checking for trial options...")
        # Try to find a "Back to Portal" or "Trial" link
        trial_link = page.get_by_role("link", name="Trial")
        if trial_link.is_visible(timeout=2000):
            trial_link.click()
        else:
            print(
                "❌ STUCK: Liferay DXP requires an activation key in this environment."
            )
            print(f"Current URL: {page.url}")
            pytest.skip("Liferay DXP Activation required. Skipping UI-dependent tests.")

    # Wait for the home page to load or check if we're at home

    try:
        page.wait_for_url(f"{liferay_url}/web/guest**", timeout=60000)
    except Exception:
        # Fallback: maybe we are already at home or redirected elsewhere
        print(f"Current URL after login: {page.url}")

    # Dismiss "Terms of Use" modal (Liferay Enterprise Search) if it appears on login
    terms_modal = page.locator(".modal-dialog", has_text="Terms of Use")
    if terms_modal.is_visible(timeout=5000):
        print("Dismissing 'Terms of Use' modal...")
        terms_modal.get_by_role("button", name="Done").click()
        terms_modal.wait_for(state="hidden", timeout=10000)

    # 2. Navigate to Fragments
    # We use the direct portlet URL for efficiency, explicitly targeting the Guest site
    fragments_url = f"{liferay_url}/group/guest/~/control_panel/manage?p_p_id=com_liferay_fragment_web_portlet_FragmentPortlet"
    page.goto(fragments_url)

    # 3. Verify Collection exists
    print("Navigating to Fragments and waiting for 'Test Collection'...")

    # In CI, the hot-deployment of the ZIP might take longer than the bash script's sleep.
    # We implement a reload loop to wait for the deployment to finish up to 150 seconds.
    max_retries = 15
    collection_found = False

    for attempt in range(max_retries):
        page.goto(fragments_url)

        # Using locator with text to be more precise in Liferay's complex UI
        collection_item = page.locator(".clay-card", has_text="Test Collection")

        # If card layout isn't used, try list layout
        if collection_item.count() == 0 or not collection_item.first.is_visible(
            timeout=2000
        ):
            collection_item = page.locator("tr", has_text="Test Collection")

        # Final fallback to generic text
        if collection_item.count() == 0 or not collection_item.first.is_visible(
            timeout=2000
        ):
            collection_item = page.get_by_text("Test Collection")

        if collection_item.count() > 0 and collection_item.first.is_visible(
            timeout=5000
        ):
            collection_found = True
            break

        print(
            f"Attempt {attempt + 1}/{max_retries}: 'Test Collection' not found yet. Reloading in 10s..."
        )
        # Use Playwright's native wait instead of time.sleep to avoid blocking the event loop
        page.wait_for_timeout(10000)

    if not collection_found:
        pytest.fail("Test Collection did not appear after 150 seconds of reloading.")

    expect(collection_item.first).to_be_visible(timeout=5000)
    print("Found 'Test Collection'. Checking fragments...")

    # 4. Click the collection and verify fragment
    # We use force=True because Liferay sometimes pops up a "Liferay Enterprise Search"
    # warning modal in trial environments that intercepts the click. The modal animation
    # timing is highly unpredictable across different environments.
    collection_item.first.click(force=True)
    fragment_item = page.get_by_text("Test Fragment")
    expect(fragment_item.first).to_be_visible(timeout=10000)

    print("✅ Fragment 'Test Fragment' found in 'Test Collection'.")
    print("✅ Fragment deployment verified via UI.")


if __name__ == "__main__":
    # Allow running directly via python if needed
    pytest.main([__file__])
