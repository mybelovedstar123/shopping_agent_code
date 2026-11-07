"""Fill the store with a varied, repeatable catalog. Safe to run more than once."""

from __future__ import annotations

import random
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .db import database_url, make_engine, make_sessions, transaction
from .models import Base, Category, Product

SEED = 91
PER_CATEGORY = 16
COLOURS = ["black", "white", "red", "blue", "green", "grey", "navy", "teal", "beige", "orange"]

# Each category: name, SKU prefix, products' words, price range in cents, attribute generators.
CATEGORIES: list[dict[str, Any]] = [
    {
        "name": "Desk Lamps",
        "prefix": "LMP",
        "noun": "Desk Lamp",
        "styles": ["Arc", "Studio", "Halo", "Slim", "Task", "Beam"],
        "price": (1499, 12900),
        "attributes": {
            "colour": COLOURS,
            "material": ["aluminium", "steel", "brass", "wood"],
            "height": ["30 cm", "38 cm", "45 cm", "52 cm", "60 cm"],
            "power": ["5 W", "8 W", "12 W", "18 W"],
            "dimmable": ["yes", "no"],
        },
    },
    {
        "name": "Mugs",
        "prefix": "MUG",
        "noun": "Mug",
        "styles": ["Harbor", "Stoneware", "Morning", "Classic", "Camp", "Tall"],
        "price": (699, 3200),
        "attributes": {
            "colour": COLOURS,
            "material": ["ceramic", "stoneware", "glass", "steel"],
            "capacity": ["250 ml", "300 ml", "350 ml", "450 ml", "600 ml"],
            "dishwasher safe": ["yes", "no"],
        },
    },
    {
        "name": "Office Chairs",
        "prefix": "CHR",
        "noun": "Office Chair",
        "styles": ["Ergo", "Task", "Mesh", "Executive", "Compact", "Pivot"],
        "price": (7900, 54900),
        "attributes": {
            "colour": ["black", "grey", "navy", "white", "beige"],
            "material": ["mesh", "leather", "fabric"],
            "weight capacity": ["100 kg", "120 kg", "150 kg", "180 kg"],
            "armrests": ["fixed", "adjustable", "none"],
            "lumbar support": ["yes", "no"],
        },
    },
    {
        "name": "Backpacks",
        "prefix": "BAG",
        "noun": "Backpack",
        "styles": ["Trail", "Metro", "Commuter", "Summit", "Daily", "Roll-top"],
        "price": (2499, 18900),
        "attributes": {
            "colour": COLOURS,
            "material": ["nylon", "canvas", "leather", "polyester"],
            "capacity": ["12 l", "18 l", "24 l", "30 l", "40 l"],
            "waterproof": ["yes", "no"],
            "laptop sleeve": ["13 inch", "15 inch", "17 inch", "none"],
        },
    },
    {
        "name": "Headphones",
        "prefix": "HPH",
        "noun": "Headphones",
        "styles": ["Studio", "Air", "Pulse", "Wave", "Pro", "Sport"],
        "price": (1999, 39900),
        "attributes": {
            "colour": ["black", "white", "silver", "blue", "red"],
            "connection": ["bluetooth", "wired", "bluetooth and wired"],
            "noise cancelling": ["yes", "no"],
            "battery life": ["20 hours", "30 hours", "40 hours", "60 hours"],
            "fit": ["over-ear", "on-ear", "in-ear"],
        },
    },
    {
        "name": "Kettles",
        "prefix": "KET",
        "noun": "Kettle",
        "styles": ["Swan", "Pour", "Rapid", "Heritage", "Glass", "Compact"],
        "price": (1999, 14900),
        "attributes": {
            "colour": ["black", "white", "red", "silver", "cream"],
            "material": ["steel", "glass", "plastic", "copper"],
            "capacity": ["0.8 l", "1 l", "1.5 l", "1.7 l", "2 l"],
            "temperature control": ["yes", "no"],
            "power": ["1500 W", "2200 W", "3000 W"],
        },
    },
    {
        "name": "Running Shoes",
        "prefix": "SHO",
        "noun": "Running Shoes",
        "styles": ["Stride", "Tempo", "Glide", "Trail", "Road", "Flex"],
        "price": (4999, 21900),
        "attributes": {
            "colour": COLOURS,
            "size": ["38", "39", "40", "41", "42", "43", "44", "45"],
            "surface": ["road", "trail", "track"],
            "cushioning": ["light", "medium", "max"],
            "gender": ["men", "women", "unisex"],
        },
    },
    {
        "name": "Water Bottles",
        "prefix": "BTL",
        "noun": "Water Bottle",
        "styles": ["Hydro", "Summit", "Sport", "Everyday", "Insulated", "Flip"],
        "price": (899, 5500),
        "attributes": {
            "colour": COLOURS,
            "material": ["steel", "plastic", "glass", "aluminium"],
            "capacity": ["500 ml", "750 ml", "1 l", "1.5 l"],
            "insulated": ["yes", "no"],
            "leakproof": ["yes", "no"],
        },
    },
    {
        "name": "Notebooks",
        "prefix": "NTB",
        "noun": "Notebook",
        "styles": ["Field", "Grid", "Classic", "Pocket", "Studio", "Travel"],
        "price": (499, 3800),
        "attributes": {
            "colour": COLOURS,
            "paper": ["lined", "grid", "dotted", "blank"],
            "pages": ["80 pages", "120 pages", "192 pages", "240 pages"],
            "cover": ["soft", "hard", "leather"],
            "size": ["A4", "A5", "A6"],
        },
    },
]


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _product_rows(spec: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    rows = []
    for number in range(1, PER_CATEGORY + 1):
        attributes = {key: rng.choice(values) for key, values in spec["attributes"].items()}
        style = rng.choice(spec["styles"])
        colour = attributes.get("colour", "")
        low, high = spec["price"]
        price = rng.randrange(low, high, 100) - 1
        title = f"{style} {spec['noun']}" + (f" in {colour}" if colour else "")
        details = ", ".join(f"{k}: {v}" for k, v in attributes.items())
        rows.append(
            {
                "sku": f"{spec['prefix']}-{number:04d}",
                "title": title,
                "description": f"The {style} {spec['noun'].lower()}. {details}.",
                "price_cents": price,
                "stock": rng.choice([0, 2, 3, 5, 8, 12, 20, 40]),
                "attributes": attributes,
            }
        )
    return rows


def seed_catalog(sessions: sessionmaker[Session]) -> int:
    """Add whatever is missing from the catalog. Returns how many products were added."""
    rng = random.Random(SEED)
    added = 0
    with transaction(sessions) as session:
        known = set(session.scalars(select(Product.sku)))
        for spec in CATEGORIES:
            category = session.scalar(select(Category).where(Category.name == spec["name"]))
            if category is None:
                category = Category(name=spec["name"], slug=slugify(spec["name"]))
                session.add(category)
            for row in _product_rows(spec, rng):
                if row["sku"] not in known:
                    session.add(Product(category=category, **row))
                    added += 1
    return added


def main() -> None:
    engine = make_engine()
    Base.metadata.create_all(engine)
    added = seed_catalog(make_sessions(engine))
    print(f"Seeded {added} new product(s) into {database_url()}")


if __name__ == "__main__":
    main()
