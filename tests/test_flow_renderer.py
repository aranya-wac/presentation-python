from app.services.flow_renderer import render_card_html, render_deck_html

_THEME = {"colors": {"primary": "#fff", "text": "#ddd", "accent": "#6ea8ff", "background": "#10131c"}}

_CARD = {
    "id": "card-1",
    "order": 1,
    "background": {"type": "color", "value": "#10131c"},
    "root": {
        "id": "s1", "type": "stack", "style": {"padding": 72, "gap": 20},
        "children": [
            {"id": "h1", "type": "heading", "content": "Hello **World**"},
            {"id": "t1", "type": "text", "content": "A paragraph."},
            {"id": "b1", "type": "bullets", "content": "One\nTwo"},
        ],
    },
}


def test_render_card_html_contains_content():
    html = render_card_html(_CARD, _THEME)
    assert "Hello" in html
    assert "<strong>World</strong>" in html  # **bold** parsed
    assert "A paragraph." in html
    assert "<li>" in html and "One" in html and "Two" in html


def test_render_card_html_escapes_text():
    card = {
        "id": "c", "order": 1,
        "root": {"id": "r", "type": "stack", "children": [
            {"id": "h", "type": "heading", "content": "a < b & c"},
        ]},
    }
    html = render_card_html(card, _THEME)
    assert "&lt; b &amp; c" in html  # raw < and & escaped


def test_render_deck_html_is_one_document():
    html = render_deck_html([_CARD, _CARD], _THEME, "My Deck")
    assert html.count("<!DOCTYPE html>") == 1
    assert "My Deck" in html
    # one card section per card
    assert html.count('data-flow-card') == 2
