
from pathlib import Path
import numpy as np
import pandas as pd

# -----------------------------------
# 1. Set up our simulated business
# -----------------------------------

rng = np.random.default_rng(2026)
data_folder = Path(__file__).resolve().parent / "data"
data_folder.mkdir(exist_ok=True)

countries = [
    "Singapore", "Malaysia", "Indonesia",
    "Thailand", "Vietnam"
]
entities = ["SPD", "QTS", "QTG"]
products = ["Product A", "Product B", "Product C"]

months = pd.date_range("2025-01-01", periods=12, freq="MS")

# -----------------------------------
# 2. Generate fictional customers
# -----------------------------------

customers = []
for country_index, country in enumerate(countries):
    for number in range(1, 11):
        customer_id = f"C{country_index * 10 + number:03d}"
        customers.append({
            "customer_id": customer_id,
            "customer_name": f"{country} Medical Customer {number}",
            "country": country,
            "business_entity": entities[(number - 1) % len(entities)]
        })

# Add two intentionally duplicated aliases for our later demo.
# Both have verified canonical IDs in this synthetic scenario.
customers.extend([
    {
        "customer_id": "C051",
        "customer_name": "SG Medical Customer One (Alias)",
        "country": "Singapore",
        "business_entity": "SPD"
    },
    {
        "customer_id": "C052",
        "customer_name": "SG Medical Customer 1",
        "country": "Singapore",
        "business_entity": "SPD"
    }
])

customers_df = pd.DataFrame(customers)

mapping_df = pd.DataFrame([
    {"original_customer_id": "C051", "canonical_customer_id": "C001"},
    {"original_customer_id": "C052", "canonical_customer_id": "C001"}
])

# -----------------------------------
# 3. Generate purchasing transactions
# -----------------------------------

orders = []
order_number = 1

for month_index, month in enumerate(months):
    for customer in customers:
        # Some customers prefer only one or two products.
        preferred_products = (
            ["Product A"] if int(customer["customer_id"][1:]) % 5 == 0
            else products
        )

        for product in preferred_products:
            if rng.random() > 0.72:
                continue

            baseline = {
                "Product A": 90,
                "Product B": 65,
                "Product C": 45
            }[product]

            country_effect = {
                "Singapore": 1.2,
                "Malaysia": 1.1,
                "Indonesia": 1.3,
                "Thailand": 0.9,
                "Vietnam": 1.0
            }[customer["country"]]

            # Some growth, seasonality, and random variation.
            trend = 1 + (month_index * 0.018)
            seasonality = 1 + 0.12 * np.sin(2 * np.pi * month_index / 12)
            noise = rng.normal(1, 0.12)

            quantity = max(
                1,
                int(baseline * country_effect * trend * seasonality * noise)
            )

            status = rng.choice(
                ["Confirmed Order", "Enquiry", "Pending Purchase Request"],
                p=[0.80, 0.12, 0.08]
            )

            orders.append({
                "order_id": f"PO{order_number:05d}",
                "customer_id": customer["customer_id"],
                "country": customer["country"],
                "business_entity": customer["business_entity"],
                "product": product,
                "order_date": (month + pd.Timedelta(days=int(rng.integers(0, 27)))).strftime("%Y-%m-%d"),
                "quantity": quantity,
                "order_status": status
            })
            order_number += 1

orders_df = pd.DataFrame(orders)

# -----------------------------------
# 4. Save the CSV files
# -----------------------------------

customers_df.to_csv(data_folder / "customers.csv", index=False)
mapping_df.to_csv(data_folder / "customer_mapping.csv", index=False)
orders_df.to_csv(data_folder / "purchase_orders.csv", index=False)

print("Success! All three datasets have been created.")
print(f"Customers: {len(customers_df)}")
print(f"Purchasing transactions: {len(orders_df)}")
print(f"Duplicate identity mappings: {len(mapping_df)}")
