-- =========================================================
-- ALGRAMMO DATA WAREHOUSE - FASE 2, STEP 1
-- Popolamento del database riconciliato dallo schema 'staging'.
-- Richiede che lo schema riconciliato esista gia' (eseguire prima
-- step3_schema_riconciliato_pg.sql, Fase 1 - Step 3) e che lo staging
-- sia stato caricato (step1_extract_load_staging_pg.py).
-- Applica solo i filtri di qualita' minimi (soft-delete esclusi, solo
-- clienti, solo righe con piatto valido). Nessun'altra trasformazione
-- sui valori: quella e' la Fase 2 - Step 2, Data Quality & Cleaning
-- (vedi step2_data_quality_cleaning_pg.py).
-- =========================================================

-- =========================================================
-- COPIA DATI: staging.* -> public.* (stesso database algrammo_dw)
-- Ordine rispettoso delle FK: cliente prima di tutto, poi a cascata.
-- =========================================================

-- CLIENTE: solo utenti con ruolo 'client', esclusi i soft-deleted
INSERT INTO cliente (id, first_name, last_name, role, company_id, created_at)
SELECT id, first_name, last_name, role, company_id, created_at
FROM staging.app_users
WHERE role = 'client'
  AND deleted_at IS NULL;

-- CLIENTE_PROFILO: solo profili di clienti gia' copiati sopra
INSERT INTO cliente_profilo (user_id, address, birth_date, height_cm, weight_kg)
SELECT cp.user_id, cp.address, cp.birth_date, cp.height_cm, cp.weight_kg
FROM staging.cliente_profilo cp
WHERE cp.deleted_at IS NULL
  AND cp.user_id IN (SELECT id FROM cliente);

-- MEAL: solo piatti attivi (non soft-deleted)
INSERT INTO meal (id, name, price, category, badge, ingredients,
                   nutrition, allergens, tags, is_custom, is_selected, created_at)
SELECT id, name, price, category, badge, ingredients,
       nutrition, allergens, tags, is_custom, is_selected, created_at
FROM staging.meal
WHERE deleted_at IS NULL;

-- ORDERS: solo ordini di clienti gia' copiati (esclude ordini di ruoli non-client)
INSERT INTO orders (id, code, client_id, company_id, order_date,
                     delivery_mode, delivery_date, delivery_address,
                     items_count, total, status)
SELECT o.id, o.code, o.client_id, o.company_id, o.order_date,
       o.delivery_mode, o.delivery_date, o.delivery_address,
       o.items_count, o.total, o.status
FROM staging.orders o
WHERE o.client_id IN (SELECT id FROM cliente);

-- ORDER_ITEM: solo righe con un piatto valido e catalogabile
-- (esclude meal_id NULL e righe che puntano a piatti soft-deleted)
INSERT INTO order_item (id, order_id, meal_id, name, quantity, unit_price, custom)
SELECT oi.id, oi.order_id, oi.meal_id, oi.name, oi.quantity, oi.unit_price, oi.custom
FROM staging.order_item oi
WHERE oi.meal_id IS NOT NULL
  AND oi.order_id IN (SELECT id FROM orders)
  AND oi.meal_id IN (SELECT id FROM meal);

-- =========================================================
-- VERIFICA RAPIDA (facoltativa, decommentare per controllo manuale)
-- =========================================================
-- SELECT 'cliente' AS tabella, COUNT(*) AS righe FROM cliente
-- UNION ALL SELECT 'cliente_profilo', COUNT(*) FROM cliente_profilo
-- UNION ALL SELECT 'meal', COUNT(*) FROM meal
-- UNION ALL SELECT 'orders', COUNT(*) FROM orders
-- UNION ALL SELECT 'order_item', COUNT(*) FROM order_item;
