"""
extra_evaluate_recommender_pg.py
----------------------------
Extra (non richiesto dalla checklist del professore) - Valutazione del
recommender: precision@k e recall@k tramite split temporale
walk-forward, settimana per settimana.

Per ogni settimana di valutazione W (a partire dalla quinta settimana
disponibile, per garantire un minimo storico di training):
  - il modello viene riallenato usando SOLO gli ordini con
    delivery_date < W (nessun data leakage dal futuro)
  - per ogni cliente con storico pre-W si generano le top-K
    raccomandazioni con la stessa logica ibrida di extra_build_recommender_pg.py
  - si confrontano con gli ordini realmente effettuati nella settimana W
  - il risultato (per ogni piatto raccomandato e/o ordinato quella
    settimana) viene scritto in reco_valutazione

Il content-based non richiede riallenamento (dipende solo dagli
attributi dei piatti, non dallo storico ordini): viene calcolato
una sola volta e riusato per ogni settimana.

Requisiti: stessi di extra_build_recommender_pg.py (deve stare nella stessa cartella)
"""

import pandas as pd
from dotenv import load_dotenv

from extra_build_recommender_pg import (
    cosine_sim_matrix, load_content_vectors, get_connection,
    TOP_K, W_CF, W_CB, W_PERSONALE
)

load_dotenv()

MIN_TRAIN_WEEKS = 4


def log(msg):
    print(f"[EVAL] {msg}")


def compute_recs_for_train_matrix(train_matrix: pd.DataFrame, sim_cb: pd.DataFrame) -> dict:
    """Ricalcola sim_cf sul solo storico di training e restituisce
    {cliente_key: [piatto_key top-K]} per tutti i clienti con storico."""
    if train_matrix.empty:
        return {}

    piatti = train_matrix.columns.tolist()
    sim_cf_arr = cosine_sim_matrix(train_matrix.T.values)
    sim_cf = pd.DataFrame(sim_cf_arr, index=piatti, columns=piatti)

    recs = {}
    for cliente_key, storico in train_matrix.iterrows():
        freq_totale = storico.sum()
        if freq_totale == 0:
            continue
        freq_norm = storico / freq_totale

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
        recs[cliente_key] = [p for p, _ in top]

    return recs


def main():
    conn = get_connection()

    log("Caricamento fact_ordini e vettori content-based...")
    fact = pd.read_sql("SELECT cliente_key, piatto_key, data_key, quantity FROM fact_ordini", conn)
    tempo = pd.read_sql("SELECT data_key, data_completa FROM dim_tempo", conn)
    fact = fact.merge(tempo, on="data_key", how="left")

    content = load_content_vectors(conn)
    sim_cb_arr = cosine_sim_matrix(content.values)
    sim_cb = pd.DataFrame(sim_cb_arr, index=content.index, columns=content.index)

    settimane = sorted(fact["data_completa"].unique())
    log(f"  Settimane totali nel dataset: {len(settimane)}")

    if len(settimane) <= MIN_TRAIN_WEEKS:
        log("Dataset troppo corto per una valutazione temporale significativa.")
        return

    settimane_valutazione = settimane[MIN_TRAIN_WEEKS:]
    log(f"  Settimane usate per la valutazione: {len(settimane_valutazione)}")

    righe_valutazione = []

    for W in settimane_valutazione:
        train_df = fact[fact["data_completa"] < W]
        test_df = fact[fact["data_completa"] == W]

        if train_df.empty or test_df.empty:
            continue

        train_matrix = train_df.pivot_table(
            index="cliente_key", columns="piatto_key", values="quantity", aggfunc="sum"
        ).fillna(0)

        recs = compute_recs_for_train_matrix(train_matrix, sim_cb)
        ordini_reali = test_df.groupby("cliente_key")["piatto_key"].apply(set).to_dict()

        clienti_coinvolti = set(recs.keys()) | set(ordini_reali.keys())
        for cliente_key in clienti_coinvolti:
            raccomandati = set(recs.get(cliente_key, []))
            ordinati = ordini_reali.get(cliente_key, set())
            unione = raccomandati | ordinati

            for piatto_key in unione:
                righe_valutazione.append((
                    int(cliente_key), int(piatto_key), W,
                    piatto_key in raccomandati,
                    piatto_key in ordinati
                ))

        log(f"  Settimana {W}: {len(clienti_coinvolti)} clienti valutati")

    log(f"Totale righe di valutazione generate: {len(righe_valutazione)}")

    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE reco_valutazione")
    insert_sql = """
        INSERT INTO reco_valutazione (cliente_key, piatto_key, settimana_valutazione,
                                       era_raccomandato, e_stato_ordinato)
        VALUES (%s, %s, %s, %s, %s)
    """
    for row in righe_valutazione:
        cur.execute(insert_sql, row)
    conn.commit()
    cur.close()

    df_val = pd.DataFrame(righe_valutazione, columns=[
        "cliente_key", "piatto_key", "settimana", "raccomandato", "ordinato"
    ])

    tp = int(((df_val["raccomandato"]) & (df_val["ordinato"])).sum())
    n_raccomandati = int(df_val["raccomandato"].sum())
    n_ordinati = int(df_val["ordinato"].sum())

    precision = tp / n_raccomandati if n_raccomandati else 0
    recall = tp / n_ordinati if n_ordinati else 0

    log(f"\n=== METRICHE AGGREGATE (K={TOP_K}) ===")
    log(f"  True positive (raccomandato E ordinato): {tp}")
    log(f"  Totale raccomandazioni valutate: {n_raccomandati}")
    log(f"  Totale ordini reali valutati: {n_ordinati}")
    log(f"  Precision@{TOP_K}: {precision:.4f}")
    log(f"  Recall@{TOP_K}: {recall:.4f}")

    conn.close()


if __name__ == "__main__":
    main()
