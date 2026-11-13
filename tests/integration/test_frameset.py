"""
Integration tests for legacy <frameset>/<frame> pages.

Playwright 1.62+ inlines <frame> content into the AI snapshot the same way as
<iframe> content. Refs inside a frame must then resolve and act, which needs
bridgic's frame scoping to match <frame> elements as well as <iframe>.
"""

from pathlib import Path

import pytest
import pytest_asyncio

from bridgic.browser.session import Browser


FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "frameset_test.html"


@pytest_asyncio.fixture
async def browser():
    b = Browser(headless=True, stealth=False, clear_user_data=True)
    yield b
    await b.close()


def _ref_for(snapshot, role: str, name: str) -> str:
    for ref, data in snapshot.refs.items():
        if data.role == role and data.name == name:
            return ref
    raise AssertionError(f"{role} {name!r} not in snapshot:\n{snapshot.tree}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_frameset_content_is_snapshotted_and_refs_resolve(browser):
    await browser.navigate_to(FIXTURE_PATH.as_uri())
    snapshot = await browser.get_snapshot()

    link_ref = _ref_for(snapshot, "link", "Left Link")
    button_ref = _ref_for(snapshot, "button", "Right Button")
    assert snapshot.refs[link_ref].frame_path == [0]
    assert snapshot.refs[button_ref].frame_path == [1]

    for ref in (link_ref, button_ref):
        locator = await browser.get_element_by_ref(ref)
        assert locator is not None, f"ref {ref} did not resolve"
        assert await locator.count() == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_click_inside_frame(browser):
    await browser.navigate_to(FIXTURE_PATH.as_uri())
    snapshot = await browser.get_snapshot()

    await browser.click_element_by_ref(_ref_for(snapshot, "button", "Right Button"))

    right = browser._page.frame(name="right")
    assert await right.locator("#right-btn").inner_text() == "Right Clicked"
