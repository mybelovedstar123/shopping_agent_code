from helpers import Notes

from kyo.agents.selection import SelectionAgent
from kyo.domain import Item, Listing


def listing(ident, price=10.0, opened=True, category="Lamps", in_stock=True, **attributes):
    return Listing(ident, f"Lamp {ident}", category, price, in_stock, attributes, "", opened)


def choose(records, needs=(), **options):
    item = options.pop("item", Item("lamp", "Lamps", options.pop("quantity", 1), list(needs)))
    payload = {"item": item.to_dict(), "records": [r.to_dict() for r in records], **options}
    return SelectionAgent().on_choose(payload, Notes())


def test_any_pick_takes_the_first_listing_that_fits():
    reply = choose(
        [listing("A", colour="blue"), listing("B", colour="red"), listing("C", colour="red")],
        ["colour: red"],
    )
    assert reply["choice"] == "B" and reply["fits"] == ["B", "C"] and reply["need_open"] == []


def test_reports_why_each_listing_was_rejected():
    reply = choose([listing("A", colour="blue")], ["colour: red"])
    assert reply["choice"] is None
    assert "colour is blue" in reply["rejected"]["A"][0]


def test_any_pick_asks_to_read_listings_only_when_nothing_fits_yet():
    unread = listing("A", opened=False)
    assert choose([unread], ["colour: red"])["need_open"] == ["A"]
    fits = listing("B", colour="red")
    assert choose([unread, fits], ["colour: red"])["need_open"] == []


def test_cheapest_pick_takes_the_lowest_price():
    reply = choose(
        [
            listing("A", 9, colour="red"),
            listing("B", 5, colour="red"),
            listing("C", 3, colour="blue"),
        ],
        ["colour: red"],
        pick="cheapest",
    )
    assert reply["choice"] == "B"


def test_cheapest_pick_reads_cheaper_unread_listings_before_choosing():
    reply = choose(
        [
            listing("A", 9, colour="red"),
            listing("B", 5, opened=False),
            listing("C", 12, opened=False),
        ],
        ["colour: red"],
        pick="cheapest",
    )
    assert reply["choice"] is None and reply["need_open"] == ["B"], "C cannot beat A"


def test_most_and_least_compare_the_stated_measure():
    records = [
        listing("A", capacity="300 ml"),
        listing("B", capacity="500 ml"),
        listing("C"),
    ]
    assert choose(records, pick="most", pick_measure="capacity")["choice"] == "B"
    assert choose(records, pick="least", pick_measure="capacity")["choice"] == "A"


def test_most_pick_reads_every_candidate_first():
    records = [listing("A", capacity="300 ml"), listing("B", opened=False)]
    reply = choose(records, pick="most", pick_measure="capacity")
    assert reply["choice"] is None and reply["need_open"] == ["B"]


def test_listing_that_states_no_measure_cannot_win():
    assert choose([listing("A")], pick="most", pick_measure="capacity")["choice"] is None


def test_wrong_kind_is_rejected():
    reply = choose([listing("A", category="Chairs")])
    assert reply["choice"] is None and "not the requested kind" in reply["rejected"]["A"][0]


def test_sold_out_is_rejected_unless_the_request_accepts_it():
    records = [listing("A", in_stock=False)]
    assert choose(records)["rejected"]["A"] == ["sold out"]
    assert choose(records, in_stock_only=False)["choice"] == "A"


def test_budget_counts_quantity_unless_it_is_per_item():
    records = [listing("A", 6)]
    assert choose(records, quantity=2, budget=10)["choice"] is None
    assert choose(records, quantity=2, budget=12)["choice"] == "A", "equal to the budget is fine"
    assert choose(records, quantity=2, budget=10, budget_scope="each")["choice"] == "A"


def test_excluded_listings_are_not_chosen():
    reply = choose([listing("A"), listing("B")], excluded=["A"])
    assert reply["choice"] == "B" and reply["rejected"]["A"] == ["ruled out earlier"]


def test_dropped_requirements_no_longer_count():
    item = Item("lamp", "Lamps", musts=["colour: red"], dropped=["colour: red"])
    assert choose([listing("A", colour="blue")], item=item)["choice"] == "A"


def test_requirements_that_cannot_be_checked_are_left_aside():
    need = "colour: unlike the lamp Ovarno sells"
    reply = choose([listing("A", colour="blue")], [need], unverified=[need])
    assert reply["choice"] == "A"


def test_wishes_break_ties_between_fits():
    records = [listing("A", colour="red"), listing("B", colour="red", shade="linen")]
    assert choose(records, ["colour: red"], wishes=["shade: linen"])["choice"] == "B"
