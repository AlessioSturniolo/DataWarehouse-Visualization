"""
step4_etl_star_schema_pg.py
-----------------------
Fase 2, Step 4 - Design ed esecuzione dell'ETL dal database riconciliato
al data warehouse: popola lo star schema (dim_*, bridge_*, fact_ordini)
a partire dalle tabelle riconciliate di algrammo_dw (schema public,
PostgreSQL).

Pattern di esecuzione: refresh completo. Ad ogni esecuzione le tabelle
dello star schema vengono svuotate (TRUNCATE, con disattivazione
temporanea dei trigger di integrita' referenziale per gestire le
dipendenze) e ripopolate da zero.

Requisiti: pip install psycopg2-binary python-dotenv
"""

import os
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor
import re

from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = dict(
    host=os.getenv("DW_PG_HOST"),
    port=int(os.getenv("DW_PG_PORT")),
    user=os.getenv("DW_PG_USER"),
    password=os.getenv("DW_PG_PASSWORD"),
    dbname=os.getenv("DW_PG_DB"),
)

NOMI_MESI = {1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile", 5: "Maggio", 6: "Giugno",
             7: "Luglio", 8: "Agosto", 9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"}
NOMI_GIORNI = {0: "Lunedì", 1: "Martedì", 2: "Mercoledì", 3: "Giovedì",
               4: "Venerdì", 5: "Sabato", 6: "Domenica"}


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def log(msg):
    print(f"[ETL] {msg}")


# ── STEP 0: svuotamento (refresh completo) ─────────────────────────

def step0_truncate():
    log("STEP 0 - Svuotamento tabelle star schema...")
    conn = get_connection()
    cur = conn.cursor()
    # Un unico TRUNCATE multi-tabella con CASCADE: gestisce automaticamente
    # anche le dipendenze verso tabelle non elencate (es. reco_* che
    # referenziano dim_piatto/dim_cliente), che vengono svuotate a cascata.
    tabelle = ["fact_ordini", "bridge_piatto_allergene", "bridge_piatto_tag",
               "bridge_piatto_ingrediente", "dim_tempo", "dim_cliente",
               "dim_piatto", "dim_zona", "dim_canale_ordine"]
    cur.execute(f"TRUNCATE TABLE {', '.join(tabelle)} RESTART IDENTITY CASCADE;")
    conn.commit()
    cur.close()
    conn.close()
    log("STEP 0 completato.\n")


# ── STEP 1: dim_tempo ────────────────────────────────────────────────

def step1_populate_dim_tempo():
    log("STEP 1 - Popolamento dim_tempo...")
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT MIN(delivery_date), MAX(delivery_date) FROM orders WHERE delivery_date IS NOT NULL")
    min_d, max_d = cur.fetchone()
    start = date(min_d.year, 1, 1)
    end = date(max_d.year, 12, 31)

    insert_sql = """
        INSERT INTO dim_tempo (data_key, data_completa, settimana_anno,
                                giorno_settimana, mese, nome_mese, trimestre, anno)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    current = start
    n = 0
    while current <= end:
        data_key = int(current.strftime("%Y%m%d"))
        cur.execute(insert_sql, (
            data_key, current, current.isocalendar()[1],
            NOMI_GIORNI[current.weekday()], current.month,
            NOMI_MESI[current.month], (current.month - 1) // 3 + 1, current.year
        ))
        n += 1
        current += timedelta(days=1)

    conn.commit()
    cur.close()
    conn.close()
    log(f"  Righe inserite: {n} (dal {start} al {end})")
    log("STEP 1 completato.\n")


# ── STEP 2: dim_cliente ──────────────────────────────────────────────

def fascia_eta(eta):
    if eta is None:
        return None
    if eta < 25:
        return "18-24"
    if eta < 35:
        return "25-34"
    if eta < 45:
        return "35-44"
    if eta < 55:
        return "45-54"
    return "55+"


def step2_populate_dim_cliente():
    log("STEP 2 - Popolamento dim_cliente...")
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT id, first_name, last_name, eta, company_id, created_at FROM cliente")
    clienti = cur.fetchall()

    insert_sql = """
        INSERT INTO dim_cliente (user_id, nome_completo, eta, fascia_eta, tipo_cliente, data_iscrizione)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    for c in clienti:
        cur.execute(insert_sql, (
            c["id"], f'{c["first_name"]} {c["last_name"]}', c["eta"],
            fascia_eta(c["eta"]),
            "aziendale" if c["company_id"] else "privato",
            c["created_at"].date()
        ))

    conn.commit()
    cur.close()
    conn.close()
    log(f"  Righe inserite: {len(clienti)}")
    log("STEP 2 completato.\n")


# ── STEP 3: dim_piatto + bridge table ────────────────────────────────

def step3_populate_dim_piatto():
    log("STEP 3 - Popolamento dim_piatto e bridge table...")
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT id, name, category, badge, nutrition, allergens, tags, ingredients FROM meal")
    piatti = cur.fetchall()

    # RETURNING piatto_key sostituisce cur.lastrowid (non esiste in Postgres)
    insert_piatto = """
        INSERT INTO dim_piatto (meal_id, nome, categoria, badge, kcal, proteine_g, carboidrati_g, grassi_g)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING piatto_key
    """
    insert_allergene = "INSERT INTO bridge_piatto_allergene (piatto_key, allergene) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    insert_tag = "INSERT INTO bridge_piatto_tag (piatto_key, tag) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    insert_ingrediente = "INSERT INTO bridge_piatto_ingrediente (piatto_key, ingrediente) VALUES (%s, %s) ON CONFLICT DO NOTHING"

    n_bridge = 0
    for p in piatti:
        # I campi jsonb sono gia' dict/list Python: psycopg2 li deserializza
        # automaticamente, a differenza di MySQL dove serviva json.loads()
        nutrition = p["nutrition"]
        cur.execute(insert_piatto, (
            p["id"], p["name"], p["category"], p["badge"],
            nutrition.get("kcal"), nutrition.get("protein"),
            nutrition.get("carbs"), nutrition.get("fat")
        ))
        piatto_key = cur.fetchone()["piatto_key"]

        for a in p["allergens"]:
            cur.execute(insert_allergene, (piatto_key, a))
            n_bridge += 1
        for t in p["tags"]:
            cur.execute(insert_tag, (piatto_key, t))
            n_bridge += 1
        for i in p["ingredients"]:
            cur.execute(insert_ingrediente, (piatto_key, i))
            n_bridge += 1

    conn.commit()
    cur.close()
    conn.close()
    log(f"  Piatti inseriti: {len(piatti)}, righe bridge totali: {n_bridge}")
    log("STEP 3 completato.\n")


# ── STEP 4: dim_zona ─────────────────────────────────────────────────

def estrai_zona(indirizzo):
    """Estrae la citta' da un indirizzo italiano tipo '... 00100 Roma (RM)'."""
    if not indirizzo:
        return None
    match = re.search(r"\d{5}\s+([A-Za-zÀ-ÿ' ]+?)\s*\(", indirizzo)
    if match:
        return match.group(1).strip()
    log(f"  ATTENZIONE: indirizzo non conforme al formato atteso, uso fallback grezzo: {indirizzo!r}")
    return indirizzo.split(",")[-1].strip()[:100]


def step4_populate_dim_zona():
    log("STEP 4 - Popolamento dim_zona...")
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Riga esplicita per indirizzo mancante (invece di escludere gli ordini)
    cur.execute("INSERT INTO dim_zona (indirizzo, citta_zona) VALUES (NULL, 'N/A')")

    cur.execute("SELECT DISTINCT delivery_address FROM orders WHERE delivery_address IS NOT NULL")
    indirizzi = cur.fetchall()

    insert_sql = "INSERT INTO dim_zona (indirizzo, citta_zona) VALUES (%s, %s)"
    for row in indirizzi:
        cur.execute(insert_sql, (row["delivery_address"], estrai_zona(row["delivery_address"])))

    conn.commit()
    cur.close()
    conn.close()
    log(f"  Righe inserite: {len(indirizzi) + 1} (incluso N/A per indirizzi mancanti)")
    log("STEP 4 completato.\n")


# ── STEP 5: dim_canale_ordine (junk dimension) ───────────────────────

def step5_populate_dim_canale_ordine():
    log("STEP 5 - Popolamento dim_canale_ordine...")
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT DISTINCT delivery_mode, status FROM orders")
    canali = cur.fetchall()

    insert_sql = "INSERT INTO dim_canale_ordine (delivery_mode, status) VALUES (%s, %s)"
    for c in canali:
        cur.execute(insert_sql, (c["delivery_mode"], c["status"]))

    conn.commit()
    cur.close()
    conn.close()
    log(f"  Combinazioni inserite: {len(canali)}")
    log("STEP 5 completato.\n")


# ── STEP 6: fact_ordini ──────────────────────────────────────────────

def step6_populate_fact_ordini():
    log("STEP 6 - Popolamento fact_ordini...")
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT cliente_key, user_id FROM dim_cliente")
    cliente_map = {r["user_id"]: r["cliente_key"] for r in cur.fetchall()}

    cur.execute("SELECT piatto_key, meal_id FROM dim_piatto")
    piatto_map = {r["meal_id"]: r["piatto_key"] for r in cur.fetchall()}

    cur.execute("SELECT data_key, data_completa FROM dim_tempo")
    tempo_map = {r["data_completa"]: r["data_key"] for r in cur.fetchall()}

    cur.execute("SELECT zona_key, indirizzo FROM dim_zona")
    zona_map = {r["indirizzo"]: r["zona_key"] for r in cur.fetchall()}
    cur.execute("SELECT zona_key FROM dim_zona WHERE indirizzo IS NULL")
    zona_na_key = cur.fetchone()["zona_key"]

    cur.execute("SELECT canale_key, delivery_mode, status FROM dim_canale_ordine")
    canale_map = {(r["delivery_mode"], r["status"]): r["canale_key"] for r in cur.fetchall()}

    cur.execute("""
        SELECT oi.id AS order_item_id, o.code AS order_code, o.client_id, oi.meal_id,
               o.delivery_date, o.delivery_address, o.delivery_mode, o.status,
               oi.quantity, oi.unit_price, oi.custom
        FROM order_item oi
        JOIN orders o ON o.id = oi.order_id
    """)
    righe = cur.fetchall()

    insert_sql = """
        INSERT INTO fact_ordini (order_item_id, order_code, cliente_key, piatto_key,
                                  data_key, zona_key, canale_key, quantity, importo_riga, is_custom)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    inserted, skipped = 0, 0
    for r in righe:
        cliente_key = cliente_map.get(r["client_id"])
        piatto_key = piatto_map.get(r["meal_id"])
        data_key = tempo_map.get(r["delivery_date"])
        zona_key = zona_map.get(r["delivery_address"]) or zona_na_key
        canale_key = canale_map.get((r["delivery_mode"], r["status"]))

        if not all([cliente_key, piatto_key, data_key, zona_key, canale_key]):
            log(f"  WARN: order_item {r['order_item_id']} - chiave non risolta, skippato.")
            skipped += 1
            continue

        importo_riga = round(float(r["quantity"]) * float(r["unit_price"]), 2)

        cur.execute(insert_sql, (
            r["order_item_id"], r["order_code"], cliente_key, piatto_key,
            data_key, zona_key, canale_key, r["quantity"], importo_riga, bool(r["custom"])
        ))
        inserted += 1

    conn.commit()
    cur.close()
    conn.close()
    log(f"  Righe inserite: {inserted}")
    if skipped:
        log(f"  Righe skippate (chiave non risolta): {skipped}")
    log("STEP 6 completato.\n")


# ── MAIN ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("=== ETL AVVIATO ===\n")
    step0_truncate()
    step1_populate_dim_tempo()
    step2_populate_dim_cliente()
    step3_populate_dim_piatto()
    step4_populate_dim_zona()
    step5_populate_dim_canale_ordine()
    step6_populate_fact_ordini()
    log("=== ETL COMPLETATO ===")
