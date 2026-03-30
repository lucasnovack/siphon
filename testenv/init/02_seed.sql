-- Seed data for Siphon extraction tests

USE testdb;

-- ── customers ─────────────────────────────────────────────────────────────────
INSERT INTO customers (name, email, country) VALUES
  ('Alice Souza',      'alice@example.com',   'BR'),
  ('Bruno Lima',       'bruno@example.com',   'BR'),
  ('Carla Mendes',     'carla@example.com',   'BR'),
  ('Daniel Costa',     'daniel@example.com',  'BR'),
  ('Eduarda Ferreira', 'eduarda@example.com', 'BR'),
  ('Felipe Rocha',     'felipe@example.com',  'BR'),
  ('Gabriela Nunes',   'gabi@example.com',    'BR'),
  ('Henrique Alves',   'henrique@example.com','BR'),
  ('Isabela Martins',  'isabela@example.com', 'BR'),
  ('João Oliveira',    'joao@example.com',    'BR');

-- ── products ──────────────────────────────────────────────────────────────────
INSERT INTO products (sku, name, price, stock) VALUES
  ('SKU-001', 'Notebook Pro 15',    4999.90, 50),
  ('SKU-002', 'Mouse Sem Fio',        89.90, 200),
  ('SKU-003', 'Teclado Mecânico',    349.90, 80),
  ('SKU-004', 'Monitor 27" 4K',    2199.90, 30),
  ('SKU-005', 'Webcam HD',           199.90, 120),
  ('SKU-006', 'Headset Gamer',       289.90, 60),
  ('SKU-007', 'SSD 1TB NVMe',        449.90, 100),
  ('SKU-008', 'Cabo USB-C 2m',        29.90, 500),
  ('SKU-009', 'Hub USB 7 portas',    149.90, 75),
  ('SKU-010', 'Suporte para Monitor', 99.90, 90);

-- ── orders ────────────────────────────────────────────────────────────────────
INSERT INTO orders (customer_id, status, amount, currency, created_at) VALUES
  (1,  'completed', 5089.80, 'BRL', '2025-01-05 10:00:00'),
  (2,  'completed',  349.90, 'BRL', '2025-01-06 11:30:00'),
  (3,  'completed', 2489.80, 'BRL', '2025-01-07 09:15:00'),
  (4,  'pending',    89.90,  'BRL', '2025-01-08 14:00:00'),
  (5,  'completed', 4999.90, 'BRL', '2025-01-09 16:45:00'),
  (6,  'cancelled',  199.90, 'BRL', '2025-01-10 08:00:00'),
  (7,  'completed',  739.80, 'BRL', '2025-01-11 13:20:00'),
  (8,  'completed', 2649.80, 'BRL', '2025-01-12 10:10:00'),
  (9,  'pending',   449.90,  'BRL', '2025-01-13 17:30:00'),
  (10, 'completed', 1299.70, 'BRL', '2025-01-14 09:00:00'),
  (1,  'completed',  289.90, 'BRL', '2025-02-01 11:00:00'),
  (3,  'completed',  149.90, 'BRL', '2025-02-03 15:00:00'),
  (5,  'pending',   4999.90, 'BRL', '2025-02-10 10:00:00'),
  (2,  'completed',  479.80, 'BRL', '2025-02-15 14:30:00'),
  (7,  'cancelled',   99.90, 'BRL', '2025-02-20 09:45:00');

-- ── order_items ───────────────────────────────────────────────────────────────
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
  (1,  1, 1, 4999.90),
  (1,  2, 1,   89.90),
  (2,  3, 1,  349.90),
  (3,  4, 1, 2199.90),
  (3,  2, 1,   89.90),
  (3,  5, 1,  199.90),
  (4,  2, 1,   89.90),
  (5,  1, 1, 4999.90),
  (6,  5, 1,  199.90),
  (7,  3, 1,  349.90),
  (7,  6, 1,  289.90),
  (7,  8, 3,   29.90),
  (8,  4, 1, 2199.90),
  (8,  7, 1,  449.90),
  (9,  7, 1,  449.90),
  (10, 9, 1,  149.90),
  (10, 3, 1,  349.90),
  (10,10, 1,   99.90),
  (10, 8,24,   29.90),
  (11, 6, 1,  289.90),
  (12, 9, 1,  149.90),
  (13, 1, 1, 4999.90),
  (14, 7, 1,  449.90),
  (14, 8, 1,   29.90),
  (15,10, 1,   99.90);
