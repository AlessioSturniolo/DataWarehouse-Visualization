-- =========================================================
-- ALGRAMMO DATA WAREHOUSE - FASE 1, STEP 5
-- Logical design: traduzione del DFM concettuale (Fase 1, Step 4)
-- in star schema fisico (DDL). Include anche le tabelle di output
-- del recommendation system (extra, non richiesto dalla checklist:
-- popolate da extra_build_recommender_pg.py), create qui perche'
-- fanno parte dello stesso schema fisico dello star schema.
-- Vive nello schema public di algrammo_dw, accanto alle tabelle
-- riconciliate (cliente, meal, orders, order_item) e allo schema
-- staging (separato).
-- =========================================================

DROP TABLE IF EXISTS reco_valutazione;
DROP TABLE IF EXISTS reco_raccomandazioni_cliente;
DROP TABLE IF EXISTS reco_similarita_piatti;
DROP TABLE IF EXISTS fact_ordini;
DROP TABLE IF EXISTS bridge_piatto_allergene;
DROP TABLE IF EXISTS bridge_piatto_tag;
DROP TABLE IF EXISTS bridge_piatto_ingrediente;
DROP TABLE IF EXISTS dim_tempo;
DROP TABLE IF EXISTS dim_cliente;
DROP TABLE IF EXISTS dim_piatto;
DROP TABLE IF EXISTS dim_zona;
DROP TABLE IF EXISTS dim_canale_ordine;

-- =========================================================
-- DIM_TEMPO
-- Gerarchia: data -> settimana -> mese -> trimestre -> anno
-- Branch: data -> giorno_settimana
-- =========================================================
CREATE TABLE dim_tempo (
    data_key         INT             NOT NULL,   -- formato YYYYMMDD
    data_completa    DATE            NOT NULL,
    settimana_anno   SMALLINT        NOT NULL,
    giorno_settimana VARCHAR(15)     NOT NULL,
    mese             SMALLINT        NOT NULL,
    nome_mese        VARCHAR(15)     NOT NULL,
    trimestre        SMALLINT        NOT NULL,
    anno             SMALLINT        NOT NULL,
    PRIMARY KEY (data_key)
);

-- =========================================================
-- DIM_CLIENTE
-- Gerarchia principale: cliente -> fascia_eta
-- Branch: cliente -> tipo_cliente
-- =========================================================
CREATE TABLE dim_cliente (
    cliente_key      INT             GENERATED ALWAYS AS IDENTITY,
    user_id          UUID            NOT NULL,      -- chiave naturale
    nome_completo    VARCHAR(200)    NOT NULL,
    eta              INT,
    fascia_eta       VARCHAR(20),
    tipo_cliente     VARCHAR(20)     NOT NULL,       -- 'privato' / 'aziendale'
    data_iscrizione  DATE            NOT NULL,
    PRIMARY KEY (cliente_key),
    UNIQUE (user_id)
);

-- =========================================================
-- DIM_PIATTO
-- Gerarchia principale: piatto -> categoria
-- Branch: piatto -> badge
-- Attributi non gerarchici: nutrition (kcal/protein/carbs/fat)
-- =========================================================
CREATE TABLE dim_piatto (
    piatto_key       INT             GENERATED ALWAYS AS IDENTITY,
    meal_id          UUID            NOT NULL,      -- chiave naturale
    nome             VARCHAR(200)    NOT NULL,
    categoria        VARCHAR(20)     NOT NULL,
    badge            VARCHAR(20),
    kcal             INT,
    proteine_g       DECIMAL(6,2),
    carboidrati_g    DECIMAL(6,2),
    grassi_g         DECIMAL(6,2),
    PRIMARY KEY (piatto_key),
    UNIQUE (meal_id)
);

-- Attributi multivalore di dim_piatto: relazione N:N via bridge table
CREATE TABLE bridge_piatto_allergene (
    piatto_key       INT             NOT NULL,
    allergene        VARCHAR(50)     NOT NULL,
    PRIMARY KEY (piatto_key, allergene),
    FOREIGN KEY (piatto_key) REFERENCES dim_piatto(piatto_key)
);

CREATE TABLE bridge_piatto_tag (
    piatto_key       INT             NOT NULL,
    tag              VARCHAR(50)     NOT NULL,
    PRIMARY KEY (piatto_key, tag),
    FOREIGN KEY (piatto_key) REFERENCES dim_piatto(piatto_key)
);

CREATE TABLE bridge_piatto_ingrediente (
    piatto_key       INT             NOT NULL,
    ingrediente      VARCHAR(100)    NOT NULL,
    PRIMARY KEY (piatto_key, ingrediente),
    FOREIGN KEY (piatto_key) REFERENCES dim_piatto(piatto_key)
);

-- =========================================================
-- DIM_ZONA
-- Gerarchia: indirizzo -> citta_zona
-- Popolata nello Step ETL tramite parsing di orders.delivery_address
-- =========================================================
CREATE TABLE dim_zona (
    zona_key         INT             GENERATED ALWAYS AS IDENTITY,
    indirizzo        VARCHAR(500),
    citta_zona       VARCHAR(100),
    PRIMARY KEY (zona_key)
);

-- =========================================================
-- DIM_CANALE_ORDINE (junk dimension)
-- Combina delivery_mode + status: attributi categorici a bassa
-- cardinalita' senza gerarchia naturale ne' tra loro ne' con
-- le altre dimensioni.
-- =========================================================
CREATE TABLE dim_canale_ordine (
    canale_key       INT             GENERATED ALWAYS AS IDENTITY,
    delivery_mode    VARCHAR(10)     NOT NULL,
    status           VARCHAR(20)     NOT NULL,
    PRIMARY KEY (canale_key),
    UNIQUE (delivery_mode, status)
);

-- =========================================================
-- FACT_ORDINI
-- Grain: un piatto per riga d'ordine (order_item).
-- Misure: quantity (additiva), importo_riga (additiva),
--         is_custom (non additiva, si aggrega con COUNT/percentuale)
-- Degenerate dimension: order_code (nessun attributo proprio,
-- non merita una dimension table separata)
-- =========================================================
CREATE TABLE fact_ordini (
    fact_key         BIGINT          GENERATED ALWAYS AS IDENTITY,
    order_item_id    UUID            NOT NULL,
    order_code       VARCHAR(20)     NOT NULL,      -- degenerate dimension
    cliente_key      INT             NOT NULL,
    piatto_key       INT             NOT NULL,
    data_key         INT             NOT NULL,
    zona_key         INT             NOT NULL,
    canale_key       INT             NOT NULL,
    quantity         INT             NOT NULL,       -- misura additiva (flow)
    importo_riga     DECIMAL(10,2)   NOT NULL,       -- misura additiva (flow)
    is_custom        BOOLEAN         NOT NULL,       -- misura non additiva
    PRIMARY KEY (fact_key),
    FOREIGN KEY (cliente_key) REFERENCES dim_cliente(cliente_key),
    FOREIGN KEY (piatto_key) REFERENCES dim_piatto(piatto_key),
    FOREIGN KEY (data_key) REFERENCES dim_tempo(data_key),
    FOREIGN KEY (zona_key) REFERENCES dim_zona(zona_key),
    FOREIGN KEY (canale_key) REFERENCES dim_canale_ordine(canale_key)
);

CREATE INDEX idx_fact_cliente ON fact_ordini(cliente_key);
CREATE INDEX idx_fact_piatto ON fact_ordini(piatto_key);
CREATE INDEX idx_fact_data ON fact_ordini(data_key);

-- =========================================================
-- TABELLE OUTPUT DEL RECOMMENDATION SYSTEM
-- Fanno parte del logical design (create qui, Step 7) ma vengono
-- popolate dallo script Python del recommender (Step 9). A
-- differenza della prima iterazione del progetto, qui sono incluse
-- fin dall'inizio nello stesso script, evitando l'omissione
-- riscontrata nella versione precedente.
-- =========================================================

-- Similarita' piatto-piatto (CF + content-based combinati)
CREATE TABLE reco_similarita_piatti (
    piatto_key_a    INT             NOT NULL,
    piatto_key_b    INT             NOT NULL,
    score_cf        DECIMAL(6,4),
    score_content   DECIMAL(6,4),
    score_finale    DECIMAL(6,4)    NOT NULL,
    PRIMARY KEY (piatto_key_a, piatto_key_b),
    FOREIGN KEY (piatto_key_a) REFERENCES dim_piatto(piatto_key),
    FOREIGN KEY (piatto_key_b) REFERENCES dim_piatto(piatto_key)
);

-- Raccomandazioni finali per cliente (top-K gia' calcolate)
CREATE TABLE reco_raccomandazioni_cliente (
    cliente_key     INT             NOT NULL,
    piatto_key      INT             NOT NULL,
    rank_posizione  INT             NOT NULL,
    score_finale    DECIMAL(6,4)    NOT NULL,
    generato_il     TIMESTAMP       NOT NULL,
    PRIMARY KEY (cliente_key, piatto_key),
    FOREIGN KEY (cliente_key) REFERENCES dim_cliente(cliente_key),
    FOREIGN KEY (piatto_key) REFERENCES dim_piatto(piatto_key)
);

-- Log per calcolare precision@k / recall@k nel tempo (per Tableau, extra_evaluate_recommender_pg.py)
CREATE TABLE reco_valutazione (
    cliente_key             INT     NOT NULL,
    piatto_key              INT     NOT NULL,
    settimana_valutazione   DATE    NOT NULL,
    era_raccomandato        BOOLEAN NOT NULL,
    e_stato_ordinato        BOOLEAN NOT NULL,
    PRIMARY KEY (cliente_key, piatto_key, settimana_valutazione),
    FOREIGN KEY (cliente_key) REFERENCES dim_cliente(cliente_key),
    FOREIGN KEY (piatto_key) REFERENCES dim_piatto(piatto_key)
);
