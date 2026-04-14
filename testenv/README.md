# testenv — Local test environment

Isolated services for end-to-end testing of Siphon without relying on external infrastructure. Completely separate from the main stack — they can be started, paused, and destroyed independently.

| Service | Role | Port |
|---------|------|-------|
| MySQL 8.0 | Data source | 3306 |
| MinIO | S3-compatible destination | 9010 / 9011 |

---

## Structure

```
testenv/
├── docker-compose.yml   # MySQL 8.0 + MinIO, named volumes
├── mysql.sh             # Control script for both services
└── init/
    ├── 01_schema.sql    # DDL: orders, customers, products, order_items
    └── 02_seed.sql      # 10 customers, 10 products, 15 orders, 25 order_items
```

Files in `init/` are executed automatically by MySQL the **first time** the container starts (when the volume is empty). To start fresh, run `destroy` followed by `start`.

---

## Commands

```bash
./testenv/mysql.sh start    # Start MySQL + MinIO and wait for healthy status
./testenv/mysql.sh stop     # Stop containers (data preserved in volumes)
./testenv/mysql.sh destroy  # Stop + remove volumes (asks for confirmation)
./testenv/mysql.sh shell    # Open MySQL CLI as the siphon user
./testenv/mysql.sh logs     # Tail logs from all services
./testenv/mysql.sh status   # Show container status
```

---

## MySQL (source)

### Credentials

| Field    | Value       |
|----------|-------------|
| Host     | `localhost` |
| Port     | `3306`      |
| Database | `testdb`    |
| User     | `siphon`    |
| Password | `siphon`    |
| Root pw  | `root`      |

### Connection string in Siphon

> Siphon runs inside Docker, so use `host.docker.internal` instead of `localhost`.

```
mysql://siphon:siphon@host.docker.internal:3306/testdb
```

### Schema

#### `customers`
| Column | Type | Description |
|---|---|---|
| `id` | INT PK | Auto increment |
| `name` | VARCHAR(100) | Full name |
| `email` | VARCHAR(150) | Unique email |
| `country` | CHAR(2) | Country code |
| `created_at` | DATETIME | Creation timestamp |

#### `products`
| Column | Type | Description |
|---|---|---|
| `id` | INT PK | Auto increment |
| `sku` | VARCHAR(50) | Unique product code |
| `name` | VARCHAR(150) | Product name |
| `price` | DECIMAL(10,2) | Unit price |
| `stock` | INT | Stock quantity |
| `updated_at` | DATETIME | Updated via `ON UPDATE` |

#### `orders`
| Column | Type | Description |
|---|---|---|
| `id` | INT PK | Auto increment |
| `customer_id` | INT FK | Reference to `customers.id` |
| `status` | VARCHAR(20) | `pending`, `completed`, `cancelled` |
| `amount` | DECIMAL(10,2) | Total order amount |
| `currency` | CHAR(3) | Currency (default: `BRL`) |
| `created_at` | DATETIME | Order timestamp |
| `updated_at` | DATETIME | Updated via `ON UPDATE` |

#### `order_items`
| Column | Type | Description |
|---|---|---|
| `id` | INT PK | Auto increment |
| `order_id` | INT FK | Reference to `orders.id` |
| `product_id` | INT FK | Reference to `products.id` |
| `quantity` | INT | Quantity |
| `unit_price` | DECIMAL(10,2) | Price at time of purchase |

### Example queries

#### Full refresh
```sql
-- Orders with customer data
SELECT
    o.id          AS order_id,
    o.status,
    o.amount,
    o.currency,
    o.created_at,
    c.name        AS customer_name,
    c.email       AS customer_email,
    c.country
FROM orders o
JOIN customers c ON c.id = o.customer_id

-- Items with product name
SELECT
    oi.order_id,
    p.sku,
    p.name        AS product_name,
    oi.quantity,
    oi.unit_price,
    oi.quantity * oi.unit_price AS subtotal
FROM order_items oi
JOIN products p ON p.id = oi.product_id
```

#### Incremental (using `updated_at`)

In Siphon, configure:
- **Extraction mode**: `incremental`
- **Watermark column**: `updated_at`

```sql
SELECT * FROM orders WHERE updated_at > '{{last_watermark}}'
```

Siphon replaces `{{last_watermark}}` with the value from the last successful run. On the first run, all rows are extracted.

#### Simulate new data for incremental testing

Open a shell with `./testenv/mysql.sh shell` and run:

```sql
-- Update an order (triggers automatic updated_at)
UPDATE orders SET status = 'completed' WHERE id = 4;

-- Insert a new order
INSERT INTO orders (customer_id, status, amount, currency)
VALUES (3, 'pending', 599.90, 'BRL');
```

On the next incremental run, only those rows will be extracted.

---

## MinIO (destination)

MinIO is an S3-compatible object store. Used as the pipeline destination in place of AWS S3.

### Credentials

| Field      | Value            |
|------------|------------------|
| Endpoint   | `localhost:9010` |
| Access Key | `minioadmin`     |
| Secret Key | `minioadmin`     |
| Console UI | http://localhost:9011 |

### Connection in Siphon

> Siphon runs inside Docker, so use `host.docker.internal` instead of `localhost`.

- **Endpoint**: `host.docker.internal:9010`
- **Access Key**: `minioadmin`
- **Secret Key**: `minioadmin`

The main `docker-compose.yml` already sets `SIPHON_S3_SCHEME=http`, so TLS is not required.

### Create a bucket before use

MinIO starts empty. You must create at least one bucket before configuring a pipeline with an S3 destination.

**Via the web console** (recommended):
1. Open http://localhost:9011
2. Login: `minioadmin` / `minioadmin`
3. Click **Buckets → Create Bucket**
4. Suggested name: `bronze`

**Via CLI** (inside the container):
```bash
docker compose -f testenv/docker-compose.yml exec minio \
  sh -c "mc alias set local http://localhost:9000 minioadmin minioadmin && mc mb local/bronze"
```

### S3 prefix in the pipeline

In the pipeline creation wizard, the **S3 Prefix** field sets the path inside the bucket where Parquet files will be written. Examples:

```
bronze/orders/
bronze/customers/
bronze/order_items/
```

Files will be written as `bronze/orders/part-0.parquet`, etc.

---

## Teardown

```bash
./testenv/mysql.sh destroy
```

Removes the containers and Docker volumes (`mysql_data` and `minio_data`). Does not affect any other Siphon services. To recreate from scratch, simply run `start` again.
