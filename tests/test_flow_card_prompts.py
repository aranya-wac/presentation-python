from app.ai.prompt_templates import FLOW_CARD_PROMPT, render


def test_flow_card_prompt_substitutes_fields():
    out = render(
        FLOW_CARD_PROMPT,
        prompt="The water cycle",
        deck_title="Understanding Water",
        card_number=3,
        total_cards=8,
        level="advanced",
    )
    assert "The water cycle" in out
    assert "Understanding Water" in out
    assert "3" in out and "8" in out
    # The model must know the body.kind vocabulary the composer understands.
    assert "callouts" in out and "iconlist" in out and "chart" in out
    # It must return a single card object, not a deck.
    assert '"cards"' not in out


from app.ai.prompt_templates import FLOW_CARD_EDIT_PROMPT


def test_flow_card_edit_prompt_substitutes_fields():
    out = render(
        FLOW_CARD_EDIT_PROMPT,
        card_json='{"id":"card-1","root":{"type":"stack"}}',
        message="make the heading shorter",
    )
    assert "card-1" in out
    assert "make the heading shorter" in out
    # The response contract must ask for both the updated card and a reply.
    assert '"card"' in out and '"reply"' in out
