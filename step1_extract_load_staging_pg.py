"""
step1_extract_load_staging_pg.py
------------------------------------------------------------
Fase 2, Step 1 (parte 1/2) - Estrazione verso staging.
Estrae le tabelle rilevanti dal database operativo Postgres (algrammo)
e le carica, senza trasformazioni sui valori, nello schema 'staging'
del Postgres dedicato al data warehouse (algrammo_dw). E' il precursore
necessario al popolamento del riconciliato (parte 2/2, vedi
step1_popolamento_riconciliato_pg.sql), dato che la sorgente qui e' un
database vivo e non un file CSV/Excel caricabile direttamente.

A differenza della versione precedente (Postgres -> MySQL), qui non
serve alcuna conversione di tipo: UUID e JSONB sono tipi nativi in
entrambi i database, quindi vengono preservati come tali (non come
stringhe) grazie alla mappatura esplicita dei dtype in df.to_sql().
"""

import os

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import UUID, JSONB
from dotenv import load_dotenv

load_dotenv()

# --- Connessioni ---------------------------------------------------------

SRC_URL = (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER')}:"
    f"{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST')}:"
    f"{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
)

DW_URL = (
    f"postgresql+psycopg2://{os.getenv('DW_PG_USER')}:"
    f"{os.getenv('DW_PG_PASSWORD')}@{os.getenv('DW_PG_HOST')}:"
    f"{os.getenv('DW_PG_PORT')}/{os.getenv('DW_PG_DB')}"
)

src_engine = create_engine(SRC_URL)
dw_engine = create_engine(DW_URL)

# --- Tabelle da estrarre (solo colonne rilevanti per il progetto) --------

TABLES = {
    "app_users": """
        SELECT id, email, first_name, last_name, role, is_active, suspended,
               company_id, created_at, deleted_at
        FROM app_users
    """,
    "cliente_profilo": """
        SELECT user_id, address, birth_date, height_cm, weight_kg, created_at, deleted_at
        FROM cliente_profilo
    """,
    "meal": """
        SELECT id, name, description, price, category, badge,
               ingredients, nutrition, allergens, tags,
               is_custom, is_selected, created_at, deleted_at
        FROM meal
    """,
    "orders": """
        SELECT id, code, client_id, company_id, order_date, delivery_mode,
               delivery_date, delivery_slot, delivery_address, items_count, total, status,
               discount_code, payment_status, created_at
        FROM orders
    """,
    "order_item": """
        SELECT id, order_id, meal_id, name, quantity, unit_price, custom, status
        FROM order_item
    """,
}

# Colonne UUID native da preservare come tali (non stringhe) nel target
UUID_COLUMNS = {
    "app_users": ["id", "company_id"],
    "cliente_profilo": ["user_id"],
    "meal": ["id"],
    "orders": ["id", "client_id", "company_id"],
    "order_item": ["id", "order_id", "meal_id"],
}

# Colonne JSONB native da preservare come tali (non stringhe JSON)
JSON_COLUMNS = {
    "meal": ["ingredients", "nutrition", "allergens", "tags"],
}


def extract_and_load(table_name: str, query: str) -> None:
    print(f"Estrazione: {table_name} ...")
    df = pd.read_sql(query, src_engine)

    dtype_map = {}
    for col in UUID_COLUMNS.get(table_name, []):
        if col in df.columns:
            dtype_map[col] = UUID(as_uuid=True)
    for col in JSON_COLUMNS.get(table_name, []):
        dtype_map[col] = JSONB

    df.to_sql(
        table_name,
        dw_engine,
        schema="staging",
        if_exists="replace",
        index=False,
        dtype=dtype_map,
    )
    print(f"  -> {len(df)} righe caricate in staging.{table_name}")


if __name__ == "__main__":
    for name, sql in TABLES.items():
        extract_and_load(name, sql)
    print("Estrazione completata.")