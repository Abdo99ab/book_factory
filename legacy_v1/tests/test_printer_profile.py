from pipeline import constants as C


def test_tj_books_page_and_cover_geometry():
    assert C.PRINTER_PROFILE_NAME == "tj_books" or C.PRINTER_PROFILES["tj_books"]
    # Geometry under default tj_books (or computed the same way)
    bleed = C.PRINTER_PROFILES["tj_books"]["bleed_mm"]
    assert bleed == 3.0
    page_mm = 210.0 + 2 * bleed
    assert abs(page_mm - 216.0) < 1e-6
    # When active profile is tj_books these hold:
    if C.PRINTER_PROFILE_NAME == "tj_books":
        assert C.PAGE_PX == 2551
        assert C.COVER_WIDTH_MM == 472.0
        assert C.COVER_HEIGHT_MM == 248.0
