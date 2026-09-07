"""
extra_generate_synthetic_data.py
------------------------------------------------------------
Extra (non richiesto dalla checklist del professore, valore aggiunto
del progetto). Genera dati sintetici realistici (clienti + ordini) e li inserisce
direttamente nel database Postgres sorgente (algrammo), cosi' che
la pipeline ETL esistente (Step 2) possa poi ricaricarli in staging
esattamente come farebbe con dati reali.

Logica di realismo:
- Ogni cliente sintetico ha una preferenza per categoria di piatto
  (dirichlet distribution: alcuni clienti concentrati su 1-2 categorie,
  altri piu' 'esploratori').
- Ogni cliente ha 2-3 piatti "del cuore" che riordina piu' spesso.
- Gli ordini sono distribuiti su N settimane (consegna il venerdi'),
  con una probabilita' di ordinare ogni settimana (non tutti i clienti
  ordinano ogni settimana, per simulare churn/irregolarita').

Questo genera un segnale realistico sia per il collaborative filtering
(pattern di co-occorrenza tra clienti simili) sia per lo storico
personale (piatti ricorrenti per singolo cliente).
"""

import os
import random
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from faker import Faker

load_dotenv()
fake = Faker("it_IT")
random.seed(42)
np.random.seed(42)

PG_URL = (
    f"postgresql+psycopg2://{os.getenv('POSTGRES_USER')}:"
    f"{os.getenv('POSTGRES_PASSWORD')}@{os.getenv('POSTGRES_HOST')}:"
    f"{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
)
engine = create_engine(PG_URL)

# --- Parametri di generazione ---------------------------------------------

N_CLIENTI = 40
N_SETTIMANE = 12
PROB_ORDINE_SETTIMANALE = 0.65
MIN_ITEM_PER_ORDINE = 2
MAX_ITEM_PER_ORDINE = 4
PROB_ORDINE_ANNULLATO = 0.05

CATEGORIE = ["primi", "secondi", "contorni", "dolci"]


def venerdi_ultime_n_settimane(n: int) -> list[date]:
    oggi = date.today()
    giorni_al_venerdi = (4 - oggi.weekday()) % 7  # 4 = venerdi'
    ultimo_venerdi = oggi + timedelta(days=giorni_al_venerdi) - timedelta(weeks=1)
    return [ultimo_venerdi - timedelta(weeks=i) for i in range(n)][::-1]


def carica_piatti() -> pd.DataFrame:
    query = "SELECT id, name, category, price FROM meal WHERE deleted_at IS NULL"
    return pd.read_sql(query, engine)


def genera_clienti(n: int) -> list[dict]:
    clienti = []
    for _ in range(n):
        user_id = str(uuid.uuid4())
        clienti.append({
            "id": user_id,
            "email": fake.unique.email(),
            "first_name": fake.first_name(),
            "last_name": fake.last_name(),
            "role": "client",
            "is_active": True,
            "suspended": False,
            "created_at": fake.date_time_between(start_date="-6M", end_date="-3M"),
            # profilo di preferenza (non salvato in DB, solo per generare ordini coerenti)
            "_pref_categoria": np.random.dirichlet(alpha=[0.6, 0.6, 0.6, 0.6]),
            "_phone": fake.phone_number()[:40],
            "_address": fake.address().replace("\n", ", ")[:255],
            "_birth_date": fake.date_of_birth(minimum_age=22, maximum_age=65),
            "_height_cm": random.randint(155, 195),
            "_weight_kg": round(random.uniform(55, 95), 2),
        })
    return clienti


def assegna_piatti_preferiti(clienti: list[dict], piatti: pd.DataFrame) -> None:
    """Assegna a ogni cliente 2-3 piatti 'del cuore' coerenti con la sua categoria preferita."""
    for c in clienti:
        cat_preferita = CATEGORIE[np.argmax(c["_pref_categoria"])]
        pool = piatti[piatti["category"] == cat_preferita]["id"].tolist()
        if len(pool) < 2:
            pool = piatti["id"].tolist()
        c["_piatti_preferiti"] = random.sample(pool, k=min(3, len(pool)))


def scegli_piatto(cliente: dict, piatti: pd.DataFrame) -> pd.Series:
    """Sceglie un piatto per una riga d'ordine, in base al profilo del cliente."""
    if random.random() < 0.55 and cliente["_piatti_preferiti"]:
        meal_id = random.choice(cliente["_piatti_preferiti"])
        return piatti[piatti["id"] == meal_id].iloc[0]

    categoria = np.random.choice(CATEGORIE, p=cliente["_pref_categoria"])
    pool = piatti[piatti["category"] == categoria]
    if pool.empty:
        pool = piatti
    return pool.sample(1).iloc[0]


def genera_ordini(clienti: list[dict], piatti: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    ordini, righe = [], []
    date_consegna = venerdi_ultime_n_settimane(N_SETTIMANE)
    contatore_ordine = 1000

    for settimana_delivery in date_consegna:
        for cliente in clienti:
            if random.random() > PROB_ORDINE_SETTIMANALE:
                continue

            order_id = str(uuid.uuid4())
            contatore_ordine += 1
            code = f"ORD-{contatore_ordine}"
            n_item = random.randint(MIN_ITEM_PER_ORDINE, MAX_ITEM_PER_ORDINE)

            righe_ordine = []
            for _ in range(n_item):
                piatto = scegli_piatto(cliente, piatti)
                quantita = 1
                righe_ordine.append({
                    "id": str(uuid.uuid4()),
                    "order_id": order_id,
                    "meal_id": piatto["id"],
                    "name": piatto["name"],
                    "quantity": quantita,
                    "unit_price": float(piatto["price"]),
                    "custom": False,
                    "status": "pronto",
                })

            totale = sum(r["unit_price"] * r["quantity"] for r in righe_ordine)
            items_count = sum(r["quantity"] for r in righe_ordine)
            annullato = random.random() < PROB_ORDINE_ANNULLATO
            order_date = settimana_delivery - timedelta(days=random.randint(1, 3))

            ordini.append({
                "id": order_id,
                "code": code,
                "client_id": cliente["id"],
                "company_id": None,
                "order_date": order_date,
                "delivery_mode": "consegna",
                "delivery_date": settimana_delivery,
                "delivery_slot": random.choice(["18:00-20:00", "19:00-21:00"]),
                "delivery_address": cliente["_address"],
                "items_count": items_count,
                "total": round(totale, 2),
                "status": "annullato" if annullato else "consegnato",
                "payment_status": "refunded" if annullato else "paid",
                "wallet_personal_amount": 0,
                "wallet_company_amount": 0,
                "refunded_amount": round(totale, 2) if annullato else 0,
                "created_at": order_date,
            })
            righe.extend(righe_ordine)

    return ordini, righe


def inserisci_nel_database(clienti, ordini, righe):
    with engine.begin() as conn:
        for c in clienti:
            conn.execute(text("""
                INSERT INTO app_users (id, email, first_name, last_name, role,
                                        is_active, suspended, created_at)
                VALUES (:id, :email, :first_name, :last_name, :role,
                        :is_active, :suspended, :created_at)
            """), {k: v for k, v in c.items() if not k.startswith("_")})

            conn.execute(text("""
                INSERT INTO cliente_profilo (user_id, phone, address, birth_date,
                                              height_cm, weight_kg, created_at)
                VALUES (:user_id, :phone, :address, :birth_date,
                        :height_cm, :weight_kg, :created_at)
            """), {
                "user_id": c["id"], "phone": c["_phone"], "address": c["_address"],
                "birth_date": c["_birth_date"], "height_cm": c["_height_cm"],
                "weight_kg": c["_weight_kg"], "created_at": c["created_at"],
            })

        for o in ordini:
            conn.execute(text("""
                INSERT INTO orders (id, code, client_id, company_id, order_date,
                                     delivery_mode, delivery_date, delivery_slot,
                                     delivery_address, items_count, total, status,
                                     payment_status, wallet_personal_amount,
                                     wallet_company_amount, refunded_amount, created_at)
                VALUES (:id, :code, :client_id, :company_id, :order_date,
                        :delivery_mode, :delivery_date, :delivery_slot,
                        :delivery_address, :items_count, :total, :status,
                        :payment_status, :wallet_personal_amount,
                        :wallet_company_amount, :refunded_amount, :created_at)
            """), o)

        for r in righe:
            conn.execute(text("""
                INSERT INTO order_item (id, order_id, meal_id, name, quantity,
                                         unit_price, custom, status)
                VALUES (:id, :order_id, :meal_id, :name, :quantity,
                        :unit_price, :custom, :status)
            """), r)


if __name__ == "__main__":
    print("Caricamento piatti esistenti...")
    piatti = carica_piatti()
    print(f"  -> {len(piatti)} piatti trovati")

    print(f"Generazione {N_CLIENTI} clienti sintetici...")
    clienti = genera_clienti(N_CLIENTI)
    assegna_piatti_preferiti(clienti, piatti)

    print(f"Generazione ordini su {N_SETTIMANE} settimane...")
    ordini, righe = genera_ordini(clienti, piatti)
    print(f"  -> {len(ordini)} ordini, {len(righe)} righe d'ordine")

    print("Inserimento nel database Postgres...")
    inserisci_nel_database(clienti, ordini, righe)
    print("Completato.")
