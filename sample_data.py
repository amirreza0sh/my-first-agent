"""Sample data in the SAME schema as the real shops.json / products.json / offers.json.

Coordinates are placed near real Tehran landmarks (see AREAS in terb_tools.py) so the
demo behaves sensibly. Replace with the real files by setting TERB_DATA_DIR.
"""

_WEEK = ["saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday"]


def _hours(default, **overrides):
    h = {d: default for d in _WEEK}
    h.update(overrides)
    return h


SHOPS = [
    {   # near Azadi Square; has instagram -> name considered real
        "id": "shop_1",
        "name": "مغازه الف",
        "address_full": "تهران، میدان آزادی، خیابان کارگر جنوبی، بلوک 5",
        "latitude": 35.7010,
        "longitude": 51.3395,
        "operating_hours": _hours("09:00-21:00", friday="تعطیل"),
        "contact": {"phone": "0210000001", "whatsapp": "+989123456789", "bale": None},
        "services": {"terb_pay": True, "installment": True, "delivery": False, "warranty": True},
        "instagram": "shop_alef_demo",
    },
    {   # near Vanak Square
        "id": "shop_2",
        "name": "مغازه ب",
        "address_full": "تهران، ونک شمالی، منطقه 12",
        "latitude": 35.7585,
        "longitude": 51.4100,
        "operating_hours": _hours("10:00-20:00"),
        "contact": {"phone": "0210000002", "whatsapp": None, "bale": None},
        "services": {"terb_pay": True, "installment": False, "delivery": False, "warranty": True},
    },
    {   # near Azadi, inside a commercial complex
        "id": "shop_3",
        "name": "مغازه ج",
        "address_full": "تهران، آزادی مرکزی، مجتمع تجاری 3",
        "latitude": 35.6985,
        "longitude": 51.3350,
        "operating_hours": _hours("09:00-19:00"),
        "contact": {"phone": "0210000003", "whatsapp": "+989198765432", "bale": "@shop_j"},
        "services": {"terb_pay": False, "installment": True, "delivery": True, "warranty": False},
    },
    {   # near Azadi, explicitly closed on Sundays
        "id": "shop_4",
        "name": "مغازه د",
        "address_full": "تهران، جاده مخصوص، نزدیک میدان آزادی",
        "latitude": 35.7050,
        "longitude": 51.3260,
        "operating_hours": _hours("09:00-21:00", sunday="closed"),
        "contact": {"phone": "0210000004", "whatsapp": "09121112233", "bale": None},
        "services": {"terb_pay": True, "installment": True, "delivery": False, "warranty": True},
    },
    {   # Grand Bazaar, no hours in the data -> status unknown
        "id": "shop_5",
        "name": "مغازه ه",
        "address_full": "تهران، بازار بزرگ، راسته کفاشان",
        "latitude": 35.6736,
        "longitude": 51.4203,
        "contact": {"phone": "0210000005", "whatsapp": None, "bale": None},
        "services": {"terb_pay": False, "installment": False, "delivery": False, "warranty": False},
    },
]

PRODUCTS = [
    {
        "id": "prod_1",
        "title_fa": "کفش نایک ایر مکس",
        "title_en": "Nike Air Max",
        "brand": "Nike",
        "category": "shoes",
        "subcategory": "casual",
        "specs": {"color": "سفید", "size": ["40", "41", "42", "43", "44"], "material": "مش", "condition": "نو"},
    },
    {
        "id": "prod_2",
        "title_fa": "کفش آدیداس استن اسمیت",
        "title_en": "Adidas Stan Smith",
        "brand": "Adidas",
        "category": "shoes",
        "subcategory": "casual",
        "specs": {"color": "سفید", "size": ["38", "39", "40", "41", "42", "43"], "material": "چرم", "condition": "نو"},
    },
    {
        "id": "prod_3",
        "title_fa": "کفش نایک رولوشن",
        "title_en": "Nike Revolution",
        "brand": "Nike",
        "category": "shoes",
        "subcategory": "sports",
        "specs": {"color": "مشکی", "size": ["41", "42", "43"], "material": "مش", "condition": "نو"},
    },
]

OFFERS = [
    # Nike Air Max
    {"product_id": "prod_1", "shop_id": "shop_1", "price": 495000, "last_updated": "2026-10-22", "availability": "in_stock"},
    {"product_id": "prod_1", "shop_id": "shop_2", "price": 520000, "last_updated": "2026-10-22", "availability": "in_stock"},
    {"product_id": "prod_1", "shop_id": "shop_3", "price": 500000, "last_updated": "2026-10-18", "availability": "in_stock"},
    {"product_id": "prod_1", "shop_id": "shop_4", "price": 490000, "last_updated": "2026-10-22", "availability": "in_stock"},
    {"product_id": "prod_1", "shop_id": "shop_5", "price": 510000, "last_updated": "2026-10-20", "availability": "unknown"},
    # Adidas Stan Smith
    {"product_id": "prod_2", "shop_id": "shop_1", "price": 450000, "last_updated": "2026-10-22", "availability": "in_stock"},
    {"product_id": "prod_2", "shop_id": "shop_3", "price": 470000, "last_updated": "2026-10-22", "availability": "in_stock"},
    {"product_id": "prod_2", "shop_id": "shop_5", "price": 430000, "last_updated": "2026-10-20", "availability": "out_of_stock"},
    # Nike Revolution
    {"product_id": "prod_3", "shop_id": "shop_2", "price": 380000, "last_updated": "2026-10-22", "availability": "in_stock"},
    {"product_id": "prod_3", "shop_id": "shop_5", "price": 390000, "last_updated": "2026-10-20", "availability": "unknown"},
]
