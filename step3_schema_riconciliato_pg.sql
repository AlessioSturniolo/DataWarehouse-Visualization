-- =========================================================
-- ALGRAMMO DATA WAREHOUSE - FASE 1, STEP 3
-- Costruzione dello schema del database riconciliato (DDL).
-- Crea lo schema riconciliato (nello schema 'public' di algrammo_dw).
-- Il popolamento dei dati da 'staging' e' un'attivita' successiva
-- (Fase 2, Step 1: vedi step1_popolamento_riconciliato_pg.sql),
-- da eseguire DOPO questo script.
-- =========================================================

-- Pulizia (utile per rieseguire lo script da zero durante lo sviluppo)
DROP TABLE IF EXISTS order_item;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS meal;
DROP TABLE IF EXISTS cliente_profilo;
DROP TABLE IF EXISTS cliente;

-- =========================================================
-- CREAZIONE SCHEMA RICONCILIATO (schema public, di default)
-- =========================================================

CREATE TABLE cliente (
    id              UUID            NOT NULL,
    first_name      VARCHAR(100)    NOT NULL,
    last_name       VARCHAR(100)    NOT NULL,
    role            VARCHAR(20)     NOT NULL,
    company_id      UUID,
    created_at      TIMESTAMP       NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE cliente_profilo (
    user_id         UUID            NOT NULL,
    address         VARCHAR(255),
    birth_date      DATE,
    height_cm       INT,
    weight_kg       DECIMAL(5,2),
    PRIMARY KEY (user_id),
    FOREIGN KEY (user_id) REFERENCES cliente(id)
);

CREATE TABLE meal (
    id              UUID            NOT NULL,
    name            VARCHAR(200)    NOT NULL,
    price           DECIMAL(8,2)    NOT NULL,
    category        VARCHAR(20)     NOT NULL,
    badge           VARCHAR(20),
    ingredients     JSONB           NOT NULL,
    nutrition       JSONB           NOT NULL,
    allergens       JSONB           NOT NULL,
    tags            JSONB           NOT NULL,
    is_custom       BOOLEAN         NOT NULL,
    is_selected     BOOLEAN         NOT NULL,
    created_at      TIMESTAMP       NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE orders (
    id              UUID            NOT NULL,
    code            VARCHAR(20)     NOT NULL,
    client_id       UUID            NOT NULL,
    company_id      UUID,
    order_date      DATE            NOT NULL,
    delivery_mode   VARCHAR(10)     NOT NULL,
    delivery_date   DATE,
    delivery_address VARCHAR(500),
    items_count     INT             NOT NULL,
    total           DECIMAL(10,2)   NOT NULL,
    status          VARCHAR(20)     NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (client_id) REFERENCES cliente(id)
);

CREATE TABLE order_item (
    id              UUID            NOT NULL,
    order_id        UUID            NOT NULL,
    meal_id         UUID            NOT NULL,
    name            VARCHAR(200)    NOT NULL,
    quantity        INT             NOT NULL,
    unit_price      DECIMAL(8,2)    NOT NULL,
    custom          BOOLEAN         NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (meal_id) REFERENCES meal(id)
);
