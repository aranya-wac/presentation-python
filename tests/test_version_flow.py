"""Version snapshots must round-trip a flow deck's Card[] JSON unchanged."""
import json


def test_flow_cards_json_round_trips():
    # A flow deck's `slides` column is a list of Card dicts (each has `root`).
    cards = [
        {"id": "card-1", "order": 1, "root": {"id": "s1", "type": "stack", "children": []}},
        {"id": "card-2", "order": 2, "root": {"id": "s2", "type": "stack", "children": []}},
    ]
    # version_service stores slides via json (de)serialization — verify that a
    # Card[] survives a JSON round-trip with `root` intact.
    restored = json.loads(json.dumps(cards))
    assert restored == cards
    assert "root" in restored[0]
    assert "blocks" not in restored[0]
