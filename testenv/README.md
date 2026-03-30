# testenv — Ambiente de testes local

Serviços isolados para testar o Siphon end-to-end sem depender de infraestrutura externa. Completamente separados do stack principal — podem ser iniciados, pausados e destruídos de forma independente.

| Serviço | Função | Porta |
|---------|--------|-------|
| MySQL 8.0 | Fonte de dados (source) | 3306 |
| MinIO | Destino S3-compatível (destination) | 9000 / 9001 |

---

## Estrutura

```
testenv/
├── docker-compose.yml   # MySQL 8.0 + MinIO, volumes nomeados
├── mysql.sh             # Script de controle de ambos os serviços
└── init/
    ├── 01_schema.sql    # DDL: orders, customers, products, order_items
    └── 02_seed.sql      # 10 customers, 10 products, 15 orders, 25 order_items
```

Os arquivos em `init/` são executados automaticamente pelo MySQL na **primeira vez** que o container sobe (quando o volume está vazio). Para re-executar do zero, use `destroy` e depois `start`.

---

## Comandos

```bash
./testenv/mysql.sh start    # Sobe MySQL + MinIO e aguarda ficarem healthy
./testenv/mysql.sh stop     # Para os containers (dados preservados nos volumes)
./testenv/mysql.sh destroy  # Para + remove os volumes (pede confirmação)
./testenv/mysql.sh shell    # Abre o MySQL CLI como usuário siphon
./testenv/mysql.sh logs     # Acompanha os logs de todos os serviços
./testenv/mysql.sh status   # Mostra estado dos containers
```

---

## MySQL (source)

### Credenciais

| Campo    | Valor       |
|----------|-------------|
| Host     | `localhost` |
| Porta    | `3306`      |
| Database | `testdb`    |
| Usuário  | `siphon`    |
| Senha    | `siphon`    |
| Root pw  | `root`      |

### Connection string no Siphon

> O Siphon roda dentro do Docker, então use `host.docker.internal` em vez de `localhost`.

```
mysql://siphon:siphon@host.docker.internal:3306/testdb
```

### Schema

#### `customers`
| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INT PK | Auto increment |
| `name` | VARCHAR(100) | Nome completo |
| `email` | VARCHAR(150) | E-mail único |
| `country` | CHAR(2) | Código do país |
| `created_at` | DATETIME | Data de criação |

#### `products`
| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INT PK | Auto increment |
| `sku` | VARCHAR(50) | Código único do produto |
| `name` | VARCHAR(150) | Nome do produto |
| `price` | DECIMAL(10,2) | Preço unitário |
| `stock` | INT | Quantidade em estoque |
| `updated_at` | DATETIME | Atualizado via `ON UPDATE` |

#### `orders`
| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INT PK | Auto increment |
| `customer_id` | INT FK | Referência a `customers.id` |
| `status` | VARCHAR(20) | `pending`, `completed`, `cancelled` |
| `amount` | DECIMAL(10,2) | Valor total do pedido |
| `currency` | CHAR(3) | Moeda (padrão: `BRL`) |
| `created_at` | DATETIME | Data do pedido |
| `updated_at` | DATETIME | Atualizado via `ON UPDATE` |

#### `order_items`
| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INT PK | Auto increment |
| `order_id` | INT FK | Referência a `orders.id` |
| `product_id` | INT FK | Referência a `products.id` |
| `quantity` | INT | Quantidade |
| `unit_price` | DECIMAL(10,2) | Preço no momento da compra |

### Queries de exemplo

#### Full refresh
```sql
-- Pedidos com dados do cliente
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

-- Itens com nome do produto
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

#### Incremental (usando `updated_at`)

No Siphon, configure:
- **Extraction mode**: `incremental`
- **Watermark column**: `updated_at`

```sql
SELECT * FROM orders WHERE updated_at > '{{last_watermark}}'
```

O Siphon substitui `{{last_watermark}}` pelo valor do último run bem-sucedido. Na primeira execução, extrai tudo.

#### Simular novos dados para testar incremental

Acesse o shell com `./testenv/mysql.sh shell` e execute:

```sql
-- Atualiza um pedido (dispara updated_at automático)
UPDATE orders SET status = 'completed' WHERE id = 4;

-- Insere novo pedido
INSERT INTO orders (customer_id, status, amount, currency)
VALUES (3, 'pending', 599.90, 'BRL');
```

Na próxima execução incremental, somente essas linhas serão extraídas.

---

## MinIO (destination)

MinIO é um object storage S3-compatível. Usado como destino dos pipelines no lugar do AWS S3.

### Credenciais

| Campo      | Valor            |
|------------|------------------|
| Endpoint   | `localhost:9000` |
| Access Key | `minioadmin`     |
| Secret Key | `minioadmin`     |
| Console UI | http://localhost:9001 |

### Connection no Siphon

> O Siphon roda dentro do Docker, então use `host.docker.internal` em vez de `localhost`.

- **Endpoint**: `host.docker.internal:9000`
- **Access Key**: `minioadmin`
- **Secret Key**: `minioadmin`

O `docker-compose.yml` principal já define `SIPHON_S3_SCHEME=http`, então não é necessário TLS.

### Criar um bucket antes de usar

O MinIO começa vazio. É necessário criar pelo menos um bucket antes de configurar um pipeline com destino S3.

**Via console web** (recomendado):
1. Acesse http://localhost:9001
2. Login: `minioadmin` / `minioadmin`
3. Clique em **Buckets → Create Bucket**
4. Nome sugerido: `bronze`

**Via CLI** (dentro do container):
```bash
docker compose -f testenv/docker-compose.yml exec minio \
  sh -c "mc alias set local http://localhost:9000 minioadmin minioadmin && mc mb local/bronze"
```

### S3 Prefix no pipeline

No wizard de criação de pipeline, o campo **S3 Prefix** define o caminho dentro do bucket onde os arquivos Parquet serão gravados. Exemplos:

```
bronze/orders/
bronze/customers/
bronze/order_items/
```

Os arquivos serão gravados como `bronze/orders/part-0.parquet`, etc.

---

## Destruir tudo

```bash
./testenv/mysql.sh destroy
```

Remove os containers e os volumes Docker (`mysql_data` e `minio_data`). Não afeta nenhum outro serviço do Siphon. Para recriar do zero, basta rodar `start` novamente.
