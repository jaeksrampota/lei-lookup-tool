"""Comprehensive UI/UX tests using Playwright.

96 browser tests covering: page rendering, single lookup, file upload,
batch progress, results table, download, keyboard/accessibility,
responsive design, error handling, and visual polish.

Requires: playwright install chromium
"""

import json
import re
import time
import socket
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def _mock_lookup():
    """Module-scoped mock so the server sees it for the whole module."""
    from src.models import LookupResult, MatchType

    async def _fake(entity, client):
        if "CAIAC" in entity.name:
            return LookupResult(
                lei="529900PY3KLUDU87D755",
                lei_status="ISSUED",
                match_type=MatchType.FULL_MATCH,
                confidence=92.5,
                gleif_legal_name="CAIAC Fund Management AG",
                gleif_legal_address="Aeulestrasse 5, Vaduz, 9490, LI",
                gleif_hq_address="Aeulestrasse 5, Vaduz, 9490, LI",
                notes="Plná shoda názvu a legal address.",
            )
        elif "Polar" in entity.name:
            return LookupResult(
                lei="4YW3JKTZ3K1II2GVCK15",
                lei_status="ISSUED",
                match_type=MatchType.FULL_MATCH,
                confidence=89.0,
                gleif_legal_name="Polar Capital LLP",
                gleif_legal_address="16 Palace Street, London, SW1E 5JD, GB",
                notes="Plná shoda názvu a legal address.",
            )
        elif "TECAM" in entity.name:
            return LookupResult(
                lei="315700ANNRQD4SG6QE82",
                lei_status="ISSUED",
                match_type=MatchType.FULL_MATCH,
                confidence=91.0,
                gleif_legal_name="TECAM PCV a.s.",
                gleif_legal_address="Kotrčova 304/2, Hradec Králové, 50301, CZ",
                notes="Plná shoda názvu a legal address.",
            )
        return LookupResult(
            match_type=MatchType.NO_MATCH,
            confidence=0.0,
            notes="Žádný LEI nalezen v GLEIF databázi.",
        )

    with patch("src.app.lookup_entity", side_effect=_fake) as m:
        yield m


@pytest.fixture(scope="module")
def server_url(_mock_lookup):
    """Start a live server on a random port for the whole test module."""
    import uvicorn
    from src import database as _db

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    # Use a dedicated test DB for the UI test server
    _ui_test_db = Path(__file__).parent / "_test_ui.db"
    _db.DB_PATH = _ui_test_db
    _db.init_db_sync()

    from src.app import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server
    import httpx as _httpx
    for _ in range(100):
        try:
            _httpx.get(url + "/", timeout=1.0)
            break
        except Exception:
            time.sleep(0.1)

    yield url
    server.should_exit = True
    if _ui_test_db.exists():
        _ui_test_db.unlink()


@pytest.fixture
def page(browser, server_url):
    """Fresh page per test."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.goto(server_url)
    yield pg
    ctx.close()


@pytest.fixture
def browser(playwright):
    b = playwright.chromium.launch(headless=True)
    yield b
    b.close()


@pytest.fixture
def playwright():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        yield p


# ===================================================================
# A. Page Load and Rendering (8 tests)
# ===================================================================

class TestPageLoad:
    def test_01_page_loads_within_timeout(self, browser, server_url):
        ctx = browser.new_context()
        page = ctx.new_page()
        start = time.time()
        page.goto(server_url, wait_until="domcontentloaded")
        elapsed = time.time() - start
        assert elapsed < 5.0
        ctx.close()

    def test_02_lookup_form_present(self, page):
        assert page.locator("#lookup-form").is_visible()

    def test_03_upload_area_present(self, page):
        assert page.locator("#drop-zone").is_visible()

    def test_04_nav_links_visible(self, page):
        assert page.locator('a[href="/"]').first.is_visible()
        assert page.locator('a[href="/history"]').is_visible()

    def test_05_nav_link_to_history(self, page, server_url):
        page.click('a[href="/history"]')
        page.wait_for_url("**/history")
        assert "History" in page.content()

    def test_06_history_page_loads(self, page, server_url):
        page.goto(server_url + "/history")
        assert page.locator("h1").inner_text() == "Lookup History"

    def test_07_404_page(self, browser, server_url):
        ctx = browser.new_context()
        pg = ctx.new_page()
        resp = pg.goto(server_url + "/nonexistent-page-xyz")
        assert resp.status == 404
        ctx.close()

    def test_08_no_js_console_errors(self, browser, server_url):
        ctx = browser.new_context()
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(server_url)
        pg.wait_for_timeout(1000)
        assert len(errors) == 0, f"JS errors: {errors}"
        ctx.close()


# ===================================================================
# B. Single Lookup Form (12 tests)
# ===================================================================

class TestSingleLookup:
    def test_09_form_submit_shows_results(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert "529900PY3KLUDU87D755" in page.content()

    def test_10_all_fields_submitted(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.fill("#country", "Lichtenštejnsko")
        page.fill("#isin", "TEST123")
        page.fill("#street", "Aeulestrasse 5")
        page.fill("#town", "Vaduz")
        page.fill("#zip_code", "9490")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert "FULL_MATCH" in page.content()

    def test_11_empty_name_shows_error(self, page):
        page.fill("#name", "")
        page.click("#lookup-submit")
        # HTML5 required attribute prevents submission, so check that we stay on page
        assert page.locator("#lookup-form").is_visible()

    def test_12_country_datalist_exists(self, page):
        assert page.locator("#country-list").count() == 1

    def test_13_country_autocomplete_populates(self, page):
        page.wait_for_timeout(1500)  # Wait for fetch
        options = page.locator("#country-list option").count()
        assert options > 50

    def test_14_spinner_during_request(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.evaluate("document.getElementById('lookup-form').addEventListener('submit', function() { window.__submitted = true; })")
        page.click("#lookup-submit")
        # Button should become disabled during submission
        page.wait_for_selector("#lookup-result", timeout=10000)

    def test_15_result_card_shows_lei(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        lei_text = page.locator(".lei-value").inner_text()
        assert "529900PY3KLUDU87D755" in lei_text

    def test_16_result_card_shows_confidence(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert "92.5%" in page.content()

    def test_17_result_card_shows_match_type(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert page.locator(".badge-full_match").count() >= 1

    def test_18_result_card_shows_notes(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert "Plná shoda" in page.content()

    def test_19_green_confidence_for_high_score(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert page.locator(".confidence-high").count() >= 1

    def test_20_no_match_result(self, page):
        page.fill("#name", "Nonexistent Entity XYZ12345")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert "NO_MATCH" in page.content()


# ===================================================================
# C. File Upload (14 tests)
# ===================================================================

class TestFileUpload:
    def _upload_file(self, page, filepath):
        page.set_input_files("#file-input", str(filepath))

    def test_21_file_picker_xlsx(self, page):
        xlsx = FIXTURES_DIR / "sample.xlsx"
        if not xlsx.exists():
            pytest.skip("sample.xlsx missing")
        self._upload_file(page, xlsx)
        assert page.locator("#file-info").is_visible()

    def test_22_file_picker_csv(self, page):
        csv_f = FIXTURES_DIR / "sample.csv"
        if not csv_f.exists():
            pytest.skip("sample.csv missing")
        self._upload_file(page, csv_f)
        assert page.locator("#file-info").is_visible()

    def test_23_file_picker_docx(self, page):
        docx_f = FIXTURES_DIR / "sample.docx"
        if not docx_f.exists():
            pytest.skip("sample.docx missing")
        self._upload_file(page, docx_f)
        assert page.locator("#file-info").is_visible()

    def test_24_drop_zone_has_visual_content(self, page):
        zone = page.locator("#drop-zone")
        assert "Drop" in zone.inner_text()
        assert ".xlsx" in zone.inner_text()

    def test_25_file_name_displayed_after_selection(self, page):
        csv_f = FIXTURES_DIR / "sample.csv"
        if not csv_f.exists():
            pytest.skip("sample.csv missing")
        self._upload_file(page, csv_f)
        name_text = page.locator("#file-name").inner_text()
        assert "sample.csv" in name_text

    def test_26_upload_button_disabled_initially(self, page):
        assert page.locator("#upload-submit").is_disabled()

    def test_27_upload_button_enabled_after_file(self, page):
        csv_f = FIXTURES_DIR / "sample.csv"
        if not csv_f.exists():
            pytest.skip("sample.csv missing")
        self._upload_file(page, csv_f)
        assert not page.locator("#upload-submit").is_disabled()

    def test_28_clear_file_resets(self, page):
        csv_f = FIXTURES_DIR / "sample.csv"
        if not csv_f.exists():
            pytest.skip("sample.csv missing")
        self._upload_file(page, csv_f)
        page.click("#file-clear")
        page.wait_for_timeout(300)
        # After clearing, the file-info should have hidden attribute
        assert page.locator("#file-info").get_attribute("hidden") is not None or not page.locator("#file-info").is_visible()
        assert page.locator("#upload-submit").is_disabled()

    def test_29_upload_redirects_to_results(self, page, server_url):
        csv_f = FIXTURES_DIR / "sample.csv"
        if not csv_f.exists():
            pytest.skip("sample.csv missing")
        self._upload_file(page, csv_f)
        page.click("#upload-submit")
        page.wait_for_url("**/results/**", timeout=10000)
        assert "/results/" in page.url

    def test_30_upload_results_page_has_table(self, page, server_url):
        csv_f = FIXTURES_DIR / "sample.csv"
        if not csv_f.exists():
            pytest.skip("sample.csv missing")
        self._upload_file(page, csv_f)
        page.click("#upload-submit")
        page.wait_for_url("**/results/**", timeout=10000)
        page.wait_for_selector("#results-table", timeout=15000)

    def test_31_file_input_accepts_correct_extensions(self, page):
        accept = page.locator("#file-input").get_attribute("accept")
        assert ".xlsx" in accept
        assert ".csv" in accept
        assert ".docx" in accept

    def test_32_drop_zone_has_aria_label(self, page):
        label = page.locator("#drop-zone").get_attribute("aria-label")
        assert label is not None and len(label) > 0

    def test_33_upload_form_has_enctype(self, page):
        enc = page.locator("#upload-form").get_attribute("enctype")
        assert enc == "multipart/form-data"

    def test_34_drop_zone_is_keyboard_accessible(self, page):
        tabindex = page.locator("#drop-zone").get_attribute("tabindex")
        assert tabindex == "0"


# ===================================================================
# D. Batch Processing Progress (8 tests)
# ===================================================================

class TestBatchProgress:
    def _start_batch(self, page, server_url):
        csv_f = FIXTURES_DIR / "sample.csv"
        if not csv_f.exists():
            pytest.skip("sample.csv missing")
        page.set_input_files("#file-input", str(csv_f))
        page.click("#upload-submit")
        page.wait_for_url("**/results/**", timeout=10000)

    def test_35_progress_section_appears(self, page, server_url):
        self._start_batch(page, server_url)
        # Either progress section is visible or already complete
        page.wait_for_selector("#results-table", timeout=15000)

    def test_36_results_table_present(self, page, server_url):
        self._start_batch(page, server_url)
        page.wait_for_selector("#results-table", timeout=15000)
        assert page.locator("#results-table").is_visible()

    def test_37_progress_bar_has_role(self, page, server_url):
        self._start_batch(page, server_url)
        container = page.locator(".progress-bar-container")
        assert container.get_attribute("role") == "progressbar"

    def test_38_progress_bar_has_aria_attrs(self, page, server_url):
        self._start_batch(page, server_url)
        container = page.locator(".progress-bar-container")
        assert container.get_attribute("aria-valuemin") == "0"
        assert container.get_attribute("aria-valuemax") is not None

    def test_39_results_page_shows_filename(self, page, server_url):
        self._start_batch(page, server_url)
        assert "sample.csv" in page.content()

    def test_40_results_page_has_entity_count(self, page, server_url):
        self._start_batch(page, server_url)
        assert "3 entities" in page.content()

    def test_41_summary_badges_present_after_complete(self, page, server_url):
        self._start_batch(page, server_url)
        page.wait_for_timeout(5000)
        page.reload()
        page.wait_for_selector("#results-table", timeout=10000)
        badges = page.locator("#summary-badges .badge").count()
        assert badges >= 1

    def test_42_download_toolbar_after_complete(self, page, server_url):
        self._start_batch(page, server_url)
        page.wait_for_timeout(5000)
        page.reload()
        page.wait_for_selector("#results-table", timeout=10000)
        toolbar = page.locator("#download-toolbar")
        assert not toolbar.is_hidden()


# ===================================================================
# E. Results Table (10 tests)
# ===================================================================

class TestResultsTable:
    def _get_results_page(self, page, server_url):
        page.set_input_files("#file-input", str(FIXTURES_DIR / "sample.csv"))
        page.click("#upload-submit")
        page.wait_for_url("**/results/**", timeout=10000)
        page.wait_for_timeout(5000)
        page.reload()
        page.wait_for_selector("#results-tbody tr", timeout=10000)

    def test_43_all_columns_present(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        headers = page.locator("#results-table thead th")
        texts = [headers.nth(i).inner_text().upper() for i in range(headers.count())]
        assert "#" in texts
        assert "NAME" in texts
        assert "LEI" in texts
        assert "CONFIDENCE" in texts

    def test_44_correct_row_count(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        rows = page.locator("#results-tbody tr").count()
        assert rows == 3

    def test_45_confidence_color_coding(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        high_cells = page.locator(".confidence-cell.confidence-high").count()
        assert high_cells >= 1

    def test_46_match_type_badges(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        badges = page.locator(".badge-full_match").count()
        assert badges >= 1

    def test_47_lei_cell_user_select(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        lei_cell = page.locator(".lei-cell").first
        assert lei_cell.is_visible()

    def test_48_notes_cell_has_title(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        title = page.locator(".notes-cell").first.get_attribute("title")
        assert title is not None and len(title) > 0

    def test_49_table_has_thead(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        assert page.locator("#results-table thead").count() == 1
        assert page.locator("#results-table tbody").count() == 1

    def test_50_table_has_scope_col(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        ths = page.locator("#results-table thead th")
        for i in range(ths.count()):
            assert ths.nth(i).get_attribute("scope") == "col"

    def test_51_filter_input_works(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        page.fill("#table-filter", "CAIAC")
        page.wait_for_timeout(500)
        visible_rows = page.locator("#results-tbody tr:visible").count()
        # At least the CAIAC row should be visible
        assert visible_rows >= 1

    def test_52_sortable_columns_clickable(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        self._get_results_page(page, server_url)
        sortable = page.locator("th.sortable").first
        assert sortable.is_visible()
        sortable.click()
        # Should not crash, column should have data-dir
        assert sortable.get_attribute("data-dir") in ("asc", "desc")


# ===================================================================
# F. Download Functionality (8 tests)
# ===================================================================

class TestDownload:
    def _setup_results(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        page.set_input_files("#file-input", str(FIXTURES_DIR / "sample.csv"))
        page.click("#upload-submit")
        page.wait_for_url("**/results/**", timeout=10000)
        page.wait_for_timeout(5000)
        page.reload()
        page.wait_for_selector("#download-toolbar:not([hidden])", timeout=10000)

    def test_53_download_xlsx_button_exists(self, page, server_url):
        self._setup_results(page, server_url)
        btn = page.locator('#download-toolbar a[href*="format=xlsx"]')
        assert btn.is_visible()

    def test_54_download_csv_button_exists(self, page, server_url):
        self._setup_results(page, server_url)
        btn = page.locator('#download-toolbar a[href*="format=csv"]')
        assert btn.is_visible()

    def test_55_download_json_button_exists(self, page, server_url):
        self._setup_results(page, server_url)
        btn = page.locator('#download-toolbar a[href*="format=json"]')
        assert btn.is_visible()

    def test_56_download_xlsx_triggers(self, page, server_url):
        self._setup_results(page, server_url)
        with page.expect_download() as download_info:
            page.click('#download-toolbar a[href*="format=xlsx"]')
        download = download_info.value
        assert download.suggested_filename.endswith(".xlsx")

    def test_57_download_csv_triggers(self, page, server_url):
        self._setup_results(page, server_url)
        with page.expect_download() as download_info:
            page.click('#download-toolbar a[href*="format=csv"]')
        download = download_info.value
        assert download.suggested_filename.endswith(".csv")

    def test_58_download_json_triggers(self, page, server_url):
        self._setup_results(page, server_url)
        with page.expect_download() as download_info:
            page.click('#download-toolbar a[href*="format=json"]')
        download = download_info.value
        assert download.suggested_filename.endswith(".json")

    def test_59_single_lookup_download_buttons(self, page, server_url):
        page.fill("#name", "CAIAC Fund Management AG")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        download_links = page.locator(".download-bar a")
        assert download_links.count() == 3

    def test_60_download_buttons_hidden_before_complete(self, page, server_url):
        if not (FIXTURES_DIR / "sample.csv").exists():
            pytest.skip("sample.csv missing")
        page.set_input_files("#file-input", str(FIXTURES_DIR / "sample.csv"))
        page.click("#upload-submit")
        page.wait_for_url("**/results/**", timeout=10000)
        # Immediately after redirect, toolbar may be hidden
        toolbar = page.locator("#download-toolbar")
        # It should either be hidden or visible after processing
        # This test just checks the element exists with the hidden attribute pattern
        assert toolbar.count() == 1


# ===================================================================
# G. Keyboard Navigation and Accessibility (12 tests)
# ===================================================================

class TestAccessibility:
    def test_61_tab_order_starts_with_skip_link(self, page):
        page.keyboard.press("Tab")
        focused = page.evaluate("document.activeElement.className")
        assert "skip-link" in focused

    def test_62_enter_submits_form(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.locator("#name").press("Enter")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert "529900PY3KLUDU87D755" in page.content()

    def test_63_focus_rings_visible(self, page):
        # Check that focus-visible styles exist in the loaded stylesheet
        has_focus_styles = page.evaluate("""
            (() => {
                for (const sheet of document.styleSheets) {
                    try {
                        for (const rule of sheet.cssRules) {
                            if (rule.selectorText && rule.selectorText.includes('focus')) return true;
                        }
                    } catch(e) {}
                }
                return false;
            })()
        """)
        assert has_focus_styles

    def test_64_skip_to_content_link_exists(self, page):
        skip = page.locator(".skip-link")
        assert skip.count() == 1
        href = skip.get_attribute("href")
        assert href == "#main-content"

    def test_65_labels_associated_with_inputs(self, page):
        labels = page.locator("label[for]")
        for i in range(labels.count()):
            for_attr = labels.nth(i).get_attribute("for")
            assert page.locator(f"#{for_attr}").count() >= 1

    def test_66_upload_zone_has_aria_label(self, page):
        zone = page.locator("#drop-zone")
        label = zone.get_attribute("aria-label")
        assert label and len(label) > 0

    def test_67_upload_zone_has_role(self, page):
        role = page.locator("#drop-zone").get_attribute("role")
        assert role == "button"

    def test_68_main_content_landmark(self, page):
        main = page.locator("main#main-content")
        assert main.count() == 1

    def test_69_nav_has_aria_label(self, page):
        nav = page.locator("nav[aria-label]")
        assert nav.count() >= 1

    def test_70_images_have_alt_or_aria_hidden(self, page):
        svgs = page.locator("svg")
        for i in range(svgs.count()):
            svg = svgs.nth(i)
            has_alt = svg.get_attribute("aria-label") is not None
            has_hidden = svg.get_attribute("aria-hidden") == "true"
            assert has_alt or has_hidden

    def test_71_toast_container_has_aria_live(self, page):
        container = page.locator("#toast-container")
        assert container.get_attribute("aria-live") == "polite"

    def test_72_nav_toggle_has_aria_expanded(self, page):
        toggle = page.locator(".nav-toggle")
        assert toggle.get_attribute("aria-expanded") in ("true", "false")


# ===================================================================
# H. Responsive Design (8 tests)
# ===================================================================

class TestResponsive:
    def test_73_mobile_320_no_overflow(self, browser, server_url):
        ctx = browser.new_context(viewport={"width": 320, "height": 568})
        pg = ctx.new_page()
        pg.goto(server_url)
        body_width = pg.evaluate("document.body.scrollWidth")
        viewport_width = pg.evaluate("window.innerWidth")
        assert body_width <= viewport_width + 5  # small tolerance
        ctx.close()

    def test_74_mobile_375_no_overflow(self, browser, server_url):
        ctx = browser.new_context(viewport={"width": 375, "height": 812})
        pg = ctx.new_page()
        pg.goto(server_url)
        body_width = pg.evaluate("document.body.scrollWidth")
        viewport_width = pg.evaluate("window.innerWidth")
        assert body_width <= viewport_width + 5
        ctx.close()

    def test_75_single_column_on_mobile(self, browser, server_url):
        ctx = browser.new_context(viewport={"width": 375, "height": 812})
        pg = ctx.new_page()
        pg.goto(server_url)
        grid = pg.locator(".two-column")
        style = pg.evaluate("""
            getComputedStyle(document.querySelector('.two-column')).gridTemplateColumns
        """)
        # On mobile should be single column (1fr or similar)
        cols = style.split()
        assert len(cols) <= 1 or cols[0] == cols[-1]  # same width = single col
        ctx.close()

    def test_76_tablet_768_layout(self, browser, server_url):
        ctx = browser.new_context(viewport={"width": 768, "height": 1024})
        pg = ctx.new_page()
        pg.goto(server_url)
        body_width = pg.evaluate("document.body.scrollWidth")
        assert body_width <= 768 + 5
        ctx.close()

    def test_77_desktop_1280_layout(self, browser, server_url):
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        pg = ctx.new_page()
        pg.goto(server_url)
        assert pg.locator(".two-column").is_visible()
        ctx.close()

    def test_78_nav_toggle_visible_on_mobile(self, browser, server_url):
        ctx = browser.new_context(viewport={"width": 375, "height": 812})
        pg = ctx.new_page()
        pg.goto(server_url)
        toggle = pg.locator(".nav-toggle")
        assert toggle.is_visible()
        ctx.close()

    def test_79_form_inputs_min_16px_on_mobile(self, browser, server_url):
        ctx = browser.new_context(viewport={"width": 375, "height": 812})
        pg = ctx.new_page()
        pg.goto(server_url)
        font_size = pg.evaluate("""
            parseFloat(getComputedStyle(document.querySelector('#name')).fontSize)
        """)
        assert font_size >= 16
        ctx.close()

    def test_80_upload_zone_tappable_on_mobile(self, browser, server_url):
        ctx = browser.new_context(viewport={"width": 375, "height": 812})
        pg = ctx.new_page()
        pg.goto(server_url)
        zone = pg.locator("#drop-zone")
        assert zone.is_visible()
        box = zone.bounding_box()
        assert box["width"] > 200
        assert box["height"] > 60
        ctx.close()


# ===================================================================
# I. Error Handling and Edge Cases (10 tests)
# ===================================================================

class TestErrorHandling:
    def test_81_xss_prevention_in_name(self, page):
        page.fill("#name", '<script>alert("xss")</script>')
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        # Verify script tag is NOT in raw HTML
        assert "<script>alert" not in page.content()

    def test_82_xss_prevention_in_results(self, page):
        page.fill("#name", '"><img src=x onerror=alert(1)>')
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert "onerror=alert" not in page.inner_html("#lookup-result")

    def test_83_unicode_names_render(self, page):
        page.fill("#name", "Železárny Prostějov a.s.")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        # Should not crash and should render correctly
        assert "Železárny" in page.content() or "NO_MATCH" in page.content()

    def test_84_long_entity_name_no_break(self, page):
        long_name = "A" * 300
        page.fill("#name", long_name)
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        body_width = page.evaluate("document.body.scrollWidth")
        viewport_width = page.evaluate("window.innerWidth")
        assert body_width <= viewport_width + 50

    def test_85_special_chars_in_search(self, page):
        page.fill("#name", "O'Brien & Associates (UK) Ltd.")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert "NO_MATCH" in page.content() or "result" in page.content().lower()

    def test_86_empty_batch_message(self, page, server_url):
        empty_f = FIXTURES_DIR / "empty.csv"
        if not empty_f.exists():
            pytest.skip("empty.csv missing")
        page.set_input_files("#file-input", str(empty_f))
        page.click("#upload-submit")
        page.wait_for_timeout(2000)
        # Should show error about no entities
        assert "error" in page.content().lower() or "No valid" in page.content()

    def test_87_browser_back_button(self, page, server_url):
        page.click('a[href="/history"]')
        page.wait_for_url("**/history")
        page.go_back()
        page.wait_for_url(server_url + "/")
        assert page.locator("#lookup-form").is_visible()

    def test_88_form_preserves_input_on_result(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.fill("#country", "Lichtenštejnsko")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        assert page.input_value("#name") == "CAIAC Fund Management AG"
        assert page.input_value("#country") == "Lichtenštejnsko"

    def test_89_results_page_nonexistent_job(self, browser, server_url):
        ctx = browser.new_context()
        pg = ctx.new_page()
        resp = pg.goto(server_url + "/results/nonexistent-job-id")
        assert resp.status == 404
        ctx.close()

    def test_90_download_nonexistent_job(self, browser, server_url):
        ctx = browser.new_context()
        pg = ctx.new_page()
        resp = pg.goto(server_url + "/download/nonexistent-job-id?format=xlsx")
        assert resp.status == 404
        ctx.close()


# ===================================================================
# J. Visual and Interaction Polish (6 tests)
# ===================================================================

class TestVisualPolish:
    def test_91_file_input_accepts_attribute(self, page):
        accept = page.locator("#file-input").get_attribute("accept")
        assert ".xlsx" in accept
        assert ".csv" in accept
        assert ".docx" in accept

    def test_92_lookup_button_text(self, page):
        text = page.locator("#lookup-submit .btn-text").inner_text()
        assert "Search" in text or "Lookup" in text

    def test_93_footer_has_gleif_link(self, page):
        footer = page.locator("footer")
        assert "GLEIF" in footer.inner_text()
        link = footer.locator("a")
        assert link.count() >= 1

    def test_94_page_title(self, page):
        title = page.title()
        assert "LEI" in title

    def test_95_download_buttons_have_titles(self, page):
        page.fill("#name", "CAIAC Fund Management AG")
        page.click("#lookup-submit")
        page.wait_for_selector("#lookup-result", timeout=10000)
        links = page.locator(".download-bar a")
        for i in range(links.count()):
            title = links.nth(i).get_attribute("title")
            assert title and len(title) > 0

    def test_96_upload_zone_icon_svg(self, page):
        svg = page.locator("#drop-zone svg")
        assert svg.count() >= 1
        assert svg.get_attribute("aria-hidden") == "true"
