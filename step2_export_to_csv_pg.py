"""
step2_export_to_csv_pg.py
----------------------
Fase 3, Step 2 (preparazione) - Esporta lo star schema (PostgreSQL) in
file CSV flat, denormalizzati, per l'uso con Tableau Public (che non
supporta connessioni dirette a database).

Vengono generati 4 file in ./tableau_export/:
  - fact_ordini_flat.csv         : fatto + tutte le dimensioni unite
  - reco_similarita_flat.csv     : matrice di similarita' piatto-piatto
  - reco_raccomandazioni_flat.csv: raccomandazioni per cliente
  - reco_valutazione_flat.csv    : log di valutazione precision@k/recall@k

Requisiti: pip install pandas psycopg2-binary python-dotenv
"""

import os
import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = dict(
    host=os.getenv("DW_PG_HOST"),
    port=int(os.getenv("DW_PG_PORT")),
    user=os.getenv("DW_PG_USER"),
    password=os.getenv("DW_PG_PASSWORD"),
    dbname=os.getenv("DW_PG_DB"),
)

OUTPUT_DIR = "tableau_export"


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def log(msg):
    print(f"[EXPORT] {msg}")


def export_fact_ordini_flat(conn):
    log("Esportazione fact_ordini_flat.csv...")
    query = """
        SELECT
            f.fact_key, f.order_item_id, f.order_code,
            f.quantity, f.importo_riga, f.is_custom,

            dt.data_completa, dt.settimana_anno, dt.giorno_settimana,
            dt.mese, dt.nome_mese, dt.trimestre, dt.anno,

            dc.cliente_key, dc.nome_completo AS cliente_nome,
            dc.eta AS cliente_eta, dc.fascia_eta, dc.tipo_cliente,
            dc.data_iscrizione,

            dp.piatto_key, dp.nome AS piatto_nome, dp.categoria,
            dp.badge, dp.kcal, dp.proteine_g, dp.carboidrati_g, dp.grassi_g,

            dz.citta_zona,

            dco.delivery_mode, dco.status
        FROM fact_ordini f
        JOIN dim_tempo dt ON dt.data_key = f.data_key
        JOIN dim_cliente dc ON dc.cliente_key = f.cliente_key
        JOIN dim_piatto dp ON dp.piatto_key = f.piatto_key
        JOIN dim_zona dz ON dz.zona_key = f.zona_key
        JOIN dim_canale_ordine dco ON dco.canale_key = f.canale_key
    """
    df = pd.read_sql(query, conn)
    path = os.path.join(OUTPUT_DIR, "fact_ordini_flat.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"  -> {len(df)} righe, {len(df.columns)} colonne -> {path}")


def export_reco_similarita_flat(conn):
    log("Esportazione reco_similarita_flat.csv...")
    query = """
        SELECT
            r.piatto_key_a, dp1.nome AS piatto_a_nome, dp1.categoria AS piatto_a_categoria,
            r.piatto_key_b, dp2.nome AS piatto_b_nome, dp2.categoria AS piatto_b_categoria,
            r.score_cf, r.score_content, r.score_finale
        FROM reco_similarita_piatti r
        JOIN dim_piatto dp1 ON dp1.piatto_key = r.piatto_key_a
        JOIN dim_piatto dp2 ON dp2.piatto_key = r.piatto_key_b
    """
    df = pd.read_sql(query, conn)
    path = os.path.join(OUTPUT_DIR, "reco_similarita_flat.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"  -> {len(df)} righe -> {path}")


def export_reco_raccomandazioni_flat(conn):
    log("Esportazione reco_raccomandazioni_flat.csv...")
    query = """
        SELECT
            r.cliente_key, dc.nome_completo AS cliente_nome,
            r.piatto_key, dp.nome AS piatto_nome, dp.categoria,
            r.rank_posizione, r.score_finale, r.generato_il
        FROM reco_raccomandazioni_cliente r
        JOIN dim_cliente dc ON dc.cliente_key = r.cliente_key
        JOIN dim_piatto dp ON dp.piatto_key = r.piatto_key
    """
    df = pd.read_sql(query, conn)
    path = os.path.join(OUTPUT_DIR, "reco_raccomandazioni_flat.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"  -> {len(df)} righe -> {path}")


def export_reco_valutazione_flat(conn):
    log("Esportazione reco_valutazione_flat.csv...")
    query = """
        SELECT
            v.cliente_key, dc.nome_completo AS cliente_nome,
            v.piatto_key, dp.nome AS piatto_nome,
            v.settimana_valutazione, v.era_raccomandato, v.e_stato_ordinato
        FROM reco_valutazione v
        JOIN dim_cliente dc ON dc.cliente_key = v.cliente_key
        JOIN dim_piatto dp ON dp.piatto_key = v.piatto_key
    """
    df = pd.read_sql(query, conn)
    path = os.path.join(OUTPUT_DIR, "reco_valutazione_flat.csv")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"  -> {len(df)} righe -> {path}")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = get_connection()

    export_fact_ordini_flat(conn)
    export_reco_similarita_flat(conn)
    export_reco_raccomandazioni_flat(conn)
    export_reco_valutazione_flat(conn)

    conn.close()
    log(f"\nEsportazione completata. File in: ./{OUTPUT_DIR}/")
