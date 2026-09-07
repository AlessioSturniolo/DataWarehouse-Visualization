"""
step2_data_quality_cleaning_pg.py
----------------------------
Fase 2, Step 2 - Data Quality Assessment e Cleaning sul database
riconciliato algrammo_dw (schema public, PostgreSQL).

Esegue tre fasi in sequenza:
  1. ANALISI   - scansiona le tabelle del riconciliato e conta le anomalie
  2. CLEANING  - corregge le anomalie con regole esplicite per campo;
                 aggiunge la colonna derivata cliente.eta
  3. VERIFICA  - rilancia query di controllo per confermare lo stato finale

Lo schema di staging NON viene toccato: tutte le modifiche avvengono
esclusivamente sulle tabelle riconciliate (schema public).
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB = dict(
    host=os.getenv("DW_PG_HOST"),
    port=int(os.getenv("DW_PG_PORT")),
    user=os.getenv("DW_PG_USER"),
    password=os.getenv("DW_PG_PASSWORD"),
    dbname=os.getenv("DW_PG_DB"),
)

SEPARATOR = "-" * 60


def connect():
    return psycopg2.connect(**DB)


# ─── FASE 1: ANALISI ──────────────────────────────────────────────────────

def analyze(cursor):
    """
    Data Quality Assessment sul database riconciliato algrammo_dw.
    Ogni check e' una query di conteggio; 0 righe = OK, altrimenti PROBLEMA.
    """
    print(SEPARATOR)
    print("REPORT DATA QUALITY - algrammo_dw (riconciliato, Postgres)")
    print(SEPARATOR)

    checks = [
        # ── TABELLA cliente ──────────────────────────────────────────────
        ("cliente", "first_name o last_name vuoti",
         "SELECT COUNT(*) FROM cliente WHERE TRIM(first_name) = '' OR TRIM(last_name) = ''"),

        # ── TABELLA cliente_profilo ──────────────────────────────────────
        ("cliente_profilo", "address NULL o vuoto",
         "SELECT COUNT(*) FROM cliente_profilo WHERE address IS NULL OR TRIM(address) = ''"),

        ("cliente_profilo", "birth_date NULL",
         "SELECT COUNT(*) FROM cliente_profilo WHERE birth_date IS NULL"),

        ("cliente_profilo", "height_cm fuori range plausibile [120,220]",
         "SELECT COUNT(*) FROM cliente_profilo WHERE height_cm IS NOT NULL AND (height_cm < 120 OR height_cm > 220)"),

        ("cliente_profilo", "weight_kg fuori range plausibile [30,200]",
         "SELECT COUNT(*) FROM cliente_profilo WHERE weight_kg IS NOT NULL AND (weight_kg < 30 OR weight_kg > 200)"),

        # ── TABELLA meal ─────────────────────────────────────────────────
        ("meal", "category fuori dominio atteso",
         "SELECT COUNT(*) FROM meal WHERE category NOT IN ('primi','secondi','contorni','dolci')"),

        # Operatore JSONB nativo Postgres: verifica che tutte le chiavi
        # attese siano presenti nell'oggetto (equivalente a
        # JSON_CONTAINS_PATH di MySQL, ma sintassi nativa piu' concisa)
        ("meal", "nutrition con chiave mancante (kcal/protein/carbs/fat)",
         """SELECT COUNT(*) FROM meal WHERE
              NOT (nutrition ?& array['kcal','protein','carbs','fat'])"""),

        # ── TABELLA orders ───────────────────────────────────────────────
        ("orders", "delivery_date NULL con delivery_mode='consegna'",
         "SELECT COUNT(*) FROM orders WHERE delivery_mode = 'consegna' AND delivery_date IS NULL"),

        # ── TABELLA order_item ───────────────────────────────────────────
        ("order_item", "quantity <= 0",
         "SELECT COUNT(*) FROM order_item WHERE quantity <= 0"),

        ("order_item", "unit_price <= 0",
         "SELECT COUNT(*) FROM order_item WHERE unit_price <= 0"),
    ]

    for tabella, problema, query in checks:
        cursor.execute(query)
        n = cursor.fetchone()[0]
        stato = "[PROBLEMA]" if n > 0 else "[OK]"
        print(f"  [{tabella}] {problema}: {n} righe  ->  {stato}")

    # ── CHECK SPECIALE: coerenza orders.total vs somma order_item ────────
    cursor.execute("""
        SELECT COUNT(*) FROM orders o
        JOIN (
            SELECT order_id, SUM(quantity * unit_price) AS totale_calcolato
            FROM order_item
            GROUP BY order_id
        ) calc ON calc.order_id = o.id
        WHERE ROUND(o.total, 2) != ROUND(calc.totale_calcolato, 2)
    """)
    n = cursor.fetchone()[0]
    print(f"  [orders] total incoerente con somma order_item: {n} righe  ->  {'[PROBLEMA]' if n > 0 else '[OK]'}")

    print()


# ─── FASE 2: CLEANING ──────────────────────────────────────────────────────

def clean(cursor, conn):
    """
    Applica le operazioni di cleaning sul database riconciliato algrammo_dw.
    """
    print(SEPARATOR)
    print("OPERAZIONI DI CLEANING")
    print(SEPARATOR)

    operations = []

    # ── OPERAZIONE 1: cliente_profilo.address ─────────────────────────────
    cursor.execute("""
        UPDATE cliente_profilo SET address = NULL
        WHERE address IS NOT NULL AND TRIM(address) = ''
    """)
    operations.append(("cliente_profilo.address", "Stringa vuota -> NULL", cursor.rowcount))

    # ── OPERAZIONE 2: correzione orders.total incoerenti ──────────────────
    # Sintassi UPDATE...FROM di Postgres (equivalente a UPDATE...JOIN di MySQL)
    cursor.execute("""
        UPDATE orders o
        SET total = ROUND(calc.totale_calcolato, 2)
        FROM (
            SELECT order_id, SUM(quantity * unit_price) AS totale_calcolato
            FROM order_item
            GROUP BY order_id
        ) calc
        WHERE calc.order_id = o.id
          AND ROUND(o.total, 2) != ROUND(calc.totale_calcolato, 2)
    """)
    operations.append(("orders.total", "Ricalcolato da somma order_item", cursor.rowcount))

    # ── OPERAZIONE 3: aggiunta colonna derivata cliente.eta ───────────────
    # information_schema.columns (minuscolo) e' lo standard SQL, seguito
    # nativamente da Postgres (a differenza di MySQL che usa maiuscolo
    # per convenzione storica sulle proprie tabelle di sistema).
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'cliente'
          AND column_name = 'eta'
    """)
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE cliente ADD COLUMN eta INT DEFAULT NULL")
        operations.append(("cliente.eta", "Colonna aggiunta", 1))

    # AGE() e DATE_PART sostituiscono TIMESTAMPDIFF (funzione MySQL-specifica)
    cursor.execute("""
        UPDATE cliente c
        SET eta = DATE_PART('year', AGE(CURRENT_DATE, cp.birth_date))
        FROM cliente_profilo cp
        WHERE cp.user_id = c.id
          AND cp.birth_date IS NOT NULL
    """)
    operations.append(("cliente.eta", "Calcolata da birth_date", cursor.rowcount))

    conn.commit()

    for campo, azione, righe in operations:
        print(f"  [{campo}] {azione}  ->  {righe} riga/e aggiornata/e")

    print()


# ─── FASE 3: VERIFICA FINALE ────────────────────────────────────────────────

def verify(cursor):
    print(SEPARATOR)
    print("VERIFICA POST-CLEANING")
    print(SEPARATOR)

    cursor.execute("SELECT COUNT(*) FROM cliente_profilo WHERE address IS NULL")
    print(f"  Clienti con address NULL: {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM cliente_profilo WHERE birth_date IS NULL")
    print(f"  Clienti con birth_date NULL: {cursor.fetchone()[0]}")

    cursor.execute("SELECT COUNT(*) FROM cliente WHERE eta IS NOT NULL")
    print(f"  Clienti con eta calcolata: {cursor.fetchone()[0]}")

    cursor.execute("""
        SELECT COUNT(*) FROM orders o
        JOIN (
            SELECT order_id, SUM(quantity * unit_price) AS totale_calcolato
            FROM order_item GROUP BY order_id
        ) calc ON calc.order_id = o.id
        WHERE ROUND(o.total, 2) != ROUND(calc.totale_calcolato, 2)
    """)
    print(f"  Ordini con total ancora incoerente: {cursor.fetchone()[0]}")

    print()
    print("Cleaning completato.")
    print(SEPARATOR)


if __name__ == "__main__":
    conn = connect()
    cursor = conn.cursor()

    analyze(cursor)
    clean(cursor, conn)
    verify(cursor)

    cursor.close()
    conn.close()
