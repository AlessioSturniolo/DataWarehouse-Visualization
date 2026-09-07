"""
extra_build_recommender_pg.py
-------------------------
Extra (non richiesto dalla checklist del professore) - Recommendation
System: calcola la similarita' piatto-piatto
(collaborative filtering item-based + content-based) e le raccomandazioni
personalizzate per cliente, scrivendo i risultati in reco_similarita_piatti
e reco_raccomandazioni_cliente (schema public di algrammo_dw, PostgreSQL).

Pesi del modello ibrido personalizzato (bilanciato familiarita'/scoperta):
  score_finale(cliente, piatto) = 0.4*CF + 0.4*content-based + 0.2*storico personale

Per la similarita' item-item (non personalizzata, usata per la heatmap in
Tableau) i due segnali sono pesati in parti uguali: 0.5*CF + 0.5*content.

Requisiti: pip install pandas numpy psycopg2-binary python-dotenv
"""

import os
from datetime import datetime

import numpy as np
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

TOP_K = 5
W_CF, W_CB, W_PERSONALE = 0.4, 0.4, 0.2


def get_connection():
    return psycopg2.connect(**DB_CONFIG)


def log(msg):
    print(f"[RECO] {msg}")


def cosine_sim_matrix(M: np.ndarray) -> np.ndarray:
    """Similarita' coseno a coppie tra le righe di M. Righe a norma zero -> similarita' 0."""
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1e-9
    Mn = M / norms
    sim = Mn @ Mn.T
    return sim


# ── STEP 1: matrice cliente x piatto (per il CF) ────────────────────────

def load_client_item_matrix(conn) -> pd.DataFrame:
    log("Caricamento matrice cliente x piatto da fact_ordini...")
    df = pd.read_sql("""
        SELECT cliente_key, piatto_key, SUM(quantity) AS freq
        FROM fact_ordini
        GROUP BY cliente_key, piatto_key
    """, conn)
    matrix = df.pivot(index="cliente_key", columns="piatto_key", values="freq").fillna(0)
    log(f"  Matrice: {matrix.shape[0]} clienti x {matrix.shape[1]} piatti")
    return matrix


# ── STEP 2: vettori di feature content-based ─────────────────────────────

def load_content_vectors(conn) -> pd.DataFrame:
    log("Costruzione vettori feature content-based da dim_piatto...")
    piatti = pd.read_sql("SELECT piatto_key, categoria, badge, kcal, proteine_g, carboidrati_g, grassi_g FROM dim_piatto", conn)

    allergeni = pd.read_sql("SELECT piatto_key, allergene FROM bridge_piatto_allergene", conn)
    tag = pd.read_sql("SELECT piatto_key, tag FROM bridge_piatto_tag", conn)

    cat_oh = pd.get_dummies(piatti.set_index("piatto_key")["categoria"], prefix="cat")
    badge_oh = pd.get_dummies(piatti.set_index("piatto_key")["badge"], prefix="badge", dummy_na=False)
    allergeni_oh = pd.crosstab(allergeni["piatto_key"], allergeni["allergene"])
    tag_oh = pd.crosstab(tag["piatto_key"], tag["tag"])

    nutrition = piatti.set_index("piatto_key")[["kcal", "proteine_g", "carboidrati_g", "grassi_g"]].fillna(0).astype(float)
    nutrition_norm = (nutrition - nutrition.min()) / (nutrition.max() - nutrition.min()).replace(0, 1)

    vettori = cat_oh.join([badge_oh, allergeni_oh, tag_oh, nutrition_norm], how="outer").fillna(0).astype(float)
    vettori = vettori.sort_index()
    log(f"  Vettori feature: {vettori.shape[0]} piatti x {vettori.shape[1]} feature")
    return vettori


# ── STEP 3: similarita' piatto-piatto (CF + content) ─────────────────────

def compute_and_store_similarity(conn, client_item: pd.DataFrame, content: pd.DataFrame):
    log("Calcolo similarita' CF e content-based...")

    # content-based copre TUTTI i piatti del menu, ma il CF vede solo quelli
    # gia' ordinati almeno una volta: si riallinea content su quel sottoinsieme
    # (ordinato) invece di pretendere che le due liste combacino esattamente.
    item_ids_cf = sorted(client_item.columns.tolist())
    sim_cf = cosine_sim_matrix(client_item[item_ids_cf].T.values)

    content_allineato = content.reindex(item_ids_cf).fillna(0.0)
    sim_cb = cosine_sim_matrix(content_allineato.values)

    ids = item_ids_cf

    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE reco_similarita_piatti")

    insert_sql = """
        INSERT INTO reco_similarita_piatti (piatto_key_a, piatto_key_b, score_cf, score_content, score_finale)
        VALUES (%s, %s, %s, %s, %s)
    """
    n = 0
    for i, a in enumerate(ids):
        for j, b in enumerate(ids):
            if a == b:
                continue
            s_cf = float(sim_cf[i, j])
            s_cb = float(sim_cb[i, j])
            s_finale = 0.5 * s_cf + 0.5 * s_cb
            cur.execute(insert_sql, (a, b, round(s_cf, 4), round(s_cb, 4), round(s_finale, 4)))
            n += 1

    conn.commit()
    cur.close()
    log(f"  Righe inserite in reco_similarita_piatti: {n}")

    return pd.DataFrame(sim_cf, index=ids, columns=ids), pd.DataFrame(sim_cb, index=ids, columns=ids)


# ── STEP 4: raccomandazioni personalizzate per cliente ───────────────────

def compute_and_store_recommendations(conn, client_item: pd.DataFrame,
                                        sim_cf: pd.DataFrame, sim_cb: pd.DataFrame):
    log("Calcolo raccomandazioni personalizzate per cliente...")

    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE reco_raccomandazioni_cliente")

    insert_sql = """
        INSERT INTO reco_raccomandazioni_cliente (cliente_key, piatto_key, rank_posizione, score_finale, generato_il)
        VALUES (%s, %s, %s, %s, %s)
    """

    ora = datetime.now()
    piatti = client_item.columns.tolist()
    n = 0

    for cliente_key, storico in client_item.iterrows():
        freq_totale = storico.sum()
        freq_norm = storico / freq_totale if freq_totale > 0 else storico

        scores = {}
        for piatto in piatti:
            piatti_ordinati = storico[storico > 0].index.tolist()
            piatti_ordinati = [p for p in piatti_ordinati if p != piatto]

            if piatti_ordinati and storico[piatti_ordinati].sum() > 0:
                pesi = storico[piatti_ordinati]
                score_cf = float((sim_cf.loc[piatto, piatti_ordinati] * pesi).sum() / pesi.sum())
                score_cb = float((sim_cb.loc[piatto, piatti_ordinati] * pesi).sum() / pesi.sum())
            else:
                score_cf, score_cb = 0.0, 0.0

            score_personale = float(freq_norm.get(piatto, 0.0))

            scores[piatto] = W_CF * score_cf + W_CB * score_cb + W_PERSONALE * score_personale

        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:TOP_K]

        for rank, (piatto_key, score) in enumerate(top, start=1):
            cur.execute(insert_sql, (int(cliente_key), int(piatto_key), rank, round(score, 4), ora))
            n += 1

    conn.commit()
    cur.close()
    log(f"  Righe inserite in reco_raccomandazioni_cliente: {n} ({len(client_item)} clienti x top {TOP_K})")


# ── MAIN ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("=== RECOMMENDATION SYSTEM - COSTRUZIONE AVVIATA ===\n")
    conn = get_connection()

    client_item = load_client_item_matrix(conn)
    content = load_content_vectors(conn)

    sim_cf, sim_cb = compute_and_store_similarity(conn, client_item, content)
    compute_and_store_recommendations(conn, client_item, sim_cf, sim_cb)

    conn.close()
    log("\n=== COMPLETATO ===")
