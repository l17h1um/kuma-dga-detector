[RU](#ru) | [EN](#en)

---

<a name="ru"></a>
# kuma-dga-detector

Детектор DGA-доменов для KUMA. ML-сервис (LightGBM) и SQL-запрос (логистическая регрессия прямо в KUMA).

## Данные для обучения

| Источник | Тип | Описание |
|---|---|---|
| `--dga` | TSV | IoC от вендора |
| `--extra` | JSONL `{domain, threat}` | Смешанный датасет, например [ExtraHop DGA Dataset](https://github.com/ExtraHop/DGA-Detection-Training-Dataset) |
| `--extra-dga` | TXT | False negatives (домены которые модель пропускает) |
| `--legit` | CSV | Топ-1М доменов Cisco или Majestic |
| `--extra-legit` | TXT | False positives (легитимные домены которые модель блокирует) |

## Обучение

```bash
python train.py \
  --dga       data/vendor_iocs.tsv \
  --extra     data/dga-training-data-example.json \
  --legit     data/top-1m.csv \
  --extra-legit data/false_positives.txt \
  --extra-dga   data/false_negatives.txt
```

Результат — `model.pkl`.

## Установка сервиса

```bash
# Собрать дистрибутив (требуется model.pkl)
bash install.sh --pack

# Установить как systemd-сервис
sudo bash install.sh --install

# С кастомными параметрами
sudo bash install.sh --install --port 8000 --no-tls --workers 16 --threshold 0.6

# Удалить
sudo bash install.sh --uninstall
```

| Флаг | По умолчанию | Описание |
|---|---|---|
| `--dir` | `/opt/dga-detector` | Директория установки |
| `--port` | `8443` / `8000` | Порт (с TLS / без) |
| `--workers` | `nproc` | Количество воркеров gunicorn |
| `--threshold` | `0.6` | Порог классификации DGA |
| `--no-tls` | — | Отключить TLS |

Сервис создаёт системного пользователя `dga-detector`, изолирован через systemd (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem`).

## API

**POST** `/predict`

```bash
curl -sk -X POST https://localhost:8443/predict \
  -H 'Content-Type: application/json' \
  -d '[{"object":"d80c31e97211.com"},{"object":"google.com"}]'
```

**GET** `/metrics` — Prometheus-метрики (batch size, latency, inference time).

## SQL-детектор (альтернатива)

`kuma-dga-query.sql` — логистическая регрессия прямо в ClickHouse-запросе KUMA. Не требует внешнего сервиса, работает как обычный запрос к событиям.

## SIEM-фильтр

Перед отправкой доменов на детект настройте whitelist-фильтр на стороне KUMA (`kuma-filter-plain.txt`) — исключите внутренние домены, PTR-записи и доверенные сервисы.

---

<a name="en"></a>
# kuma-dga-detector

DGA domain detector for KUMA. Two modes: ML service (LightGBM) and SQL query (logistic regression inside KUMA).

## Training Data

| Source | Format | Description |
|---|---|---|
| `--dga` | TSV | Vendor IoC feed |
| `--extra` | JSONL `{domain, threat}` | Mixed dataset, e.g. [ExtraHop DGA Dataset](https://github.com/ExtraHop/DGA-Detection-Training-Dataset) |
| `--extra-dga` | TXT | False negatives (domains the model misses) |
| `--legit` | CSV | Cisco or Majestic top-1M |
| `--extra-legit` | TXT | False positives (legitimate domains incorrectly flagged) |

## Training

```bash
python train.py \
  --dga       data/vendor_iocs.tsv \
  --extra     data/dga-training-data-example.json \
  --legit     data/top-1m.csv \
  --extra-legit data/false_positives.txt \
  --extra-dga   data/false_negatives.txt
```

Output: `model.pkl`.

## Installation

```bash
# Pack distributable (model.pkl required)
bash install.sh --pack

# Install as systemd service
sudo bash install.sh --install

# Custom parameters
sudo bash install.sh --install --port 8000 --no-tls --workers 16 --threshold 0.6

# Uninstall
sudo bash install.sh --uninstall
```

| Flag | Default | Description |
|---|---|---|
| `--dir` | `/opt/dga-detector` | Installation directory |
| `--port` | `8443` / `8000` | Port (with TLS / without) |
| `--workers` | `nproc` | Gunicorn worker count |
| `--threshold` | `0.6` | DGA classification threshold |
| `--no-tls` | — | Disable TLS |

The service runs as a dedicated `dga-detector` system user, isolated via systemd (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem`).

## API

**POST** `/predict`

```bash
curl -sk -X POST https://localhost:8443/predict \
  -H 'Content-Type: application/json' \
  -d '[{"object":"d80c31e97211.com"},{"object":"google.com"}]'
```

**GET** `/metrics` — Prometheus metrics (batch size, latency, inference time).

## SQL Detector (alternative)

`kuma-dga-query.sql` — logistic regression expressed as a ClickHouse query inside KUMA. No external service needed, runs as a regular event query.

## SIEM Filter

Before sending domains for detection, configure a whitelist filter on the KUMA side (`kuma-filter-plain.txt`) — exclude internal domains, PTR records, and trusted services.
