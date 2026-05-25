from app.schemas.presentation import PresentationDetail, ThemeSchema


def _base_kwargs():
    return dict(
        id="p1", title="T", description="", template_id="t1", template_name="",
        theme_id="th1", is_preview=False, total_slides=0, slide_count=0,
        created_at="", updated_at="", slides=[], logo_url="",
    )


def test_presentation_detail_theme_defaults_none():
    d = PresentationDetail(**_base_kwargs())
    assert d.theme is None


def test_presentation_detail_accepts_theme():
    d = PresentationDetail(
        **_base_kwargs(),
        theme=ThemeSchema(id="th1", name="Dark", colors={"primary": "#fff"}, fonts={}),
    )
    assert d.theme is not None
    assert d.theme.colors["primary"] == "#fff"
