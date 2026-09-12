# 🚀 SyncTally Realtime Integration Documentation
### Tally Connector API & Neon Database Specification

This document provides all technical specifications, REST API endpoints, Neon PostgreSQL connection strings, table schemas, and code samples for the **Tally Development Team** to automatically push realtime summary figures into the dashboard.

---

## 1. Overview & Architecture

Whenever Tally reconciles or at periodic intervals (e.g. hourly or end-of-day), the Tally connector sends **3 core financial figures** for each educational entity:
1. **`opening_balance`** (Previous session opening receivable)
2. **`due_amount`** (Total student fee billed / due in current session)
3. **`receipt_amount`** (Total fees collected / payment receipts received)

The dashboard system automatically calculates the 4th row:
$$\text{Calculated Balance} = (\text{Opening Balance} + \text{Due Amount}) - \text{Receipt Amount}$$

Data is stored in **Neon PostgreSQL** and instantly reflects on the executive web dashboard:
- **Live Production URL**: [https://dashboard-chi-two-41.vercel.app](https://dashboard-chi-two-41.vercel.app)
- **Local Dev URL**: `http://127.0.0.1:9000`

---

## 2. Recommended Method: Secure REST Webhook API

The simplest and most reliable integration is making an HTTP `POST` request from the Tally script or middleware directly to the cloud dashboard endpoint.

### Endpoints

| Environment | Method | Endpoint URL |
| :--- | :--- | :--- |
| **Production (Vercel)** | `POST` | `https://dashboard-chi-two-41.vercel.app/api/tally` |
| **Local Testing** | `POST` | `http://127.0.0.1:9000/api/tally` |

### Headers
```http
Content-Type: application/json
```

---

### Request Payload Specification (JSON)

| Field Name | Type | Required | Description | Example |
| :--- | :--- | :--- | :--- | :--- |
| `company_id` | `string` / `number` | **Yes** | Internal Company ID matching portal institution | `"1"` |
| `entity` | `string` | **Yes** | Target institution type: `"school"` or `"college"` | `"school"` |
| `opening_balance` | `number` | **Yes** | Total Opening Balance amount | `14403105.42` |
| `due_amount` | `number` | **Yes** | Total Fee Due amount for the period | `685497604.00` |
| `receipt_amount` | `number` | **Yes** | Total Fee Receipts collected for the period | `559939981.02` |

> [!TIP]
> **Dates & Balance are fully automated:**
> 1. `from_date` (Current Financial Year start, e.g. `2026-04-01`) and `to_date` (Today's sync date) are **automatically determined by the server** on each update. You do **not** need to send them.
> 2. `net_balance` is also **automatically calculated** by the system as $(\text{opening} + \text{due}) - \text{receipt}$.

---

### Minimal Request Body (JSON)

```json
{
  "entity": "school",
  "company_id": "1",
  "opening_balance": 14403105.42,
  "due_amount": 685497604.00,
  "receipt_amount": 559939981.02
}
```

---

### Sample Success Response (`200 OK`)

```json
{
  "status": "success",
  "message": "Tally 3 metrics synced and balance calculated successfully into Neon DB",
  "data": {
    "id": 1,
    "table": "tally_school_summary",
    "company_id": "1",
    "from_date": "2026-04-01",
    "to_date": "2026-09-12",
    "opening_balance": 14403105.42,
    "due_amount": 685497604.00,
    "receipt_amount": 559939981.02,
    "calculated_balance": 140960728.40,
    "updated_at": "2026-09-12 07:45:00+00:00"
  }
}
```

---

## 3. Alternative Method: Direct Neon PostgreSQL Database Connection

If your Tally integration connects directly to SQL databases via ODBC/ODBC-PostgreSQL or Python/Node.js, you can connect directly to Neon PostgreSQL.

### Database Connection Credentials

| Parameter | Value |
| :--- | :--- |
| **Connection String (Pooled)** | `postgresql://neondb_owner:npg_feZl30PimbRI@ep-solitary-scene-aea7vq5m-pooler.c-2.us-east-2.aws.neon.tech/neondb?sslmode=require` |
| **Direct Host** | `ep-solitary-scene-aea7vq5m.c-2.us-east-2.aws.neon.tech` |
| **Pooled Host** | `ep-solitary-scene-aea7vq5m-pooler.c-2.us-east-2.aws.neon.tech` |
| **Port** | `5432` |
| **Database Name** | `neondb` |
| **User** | `neondb_owner` |
| **Password** | `npg_feZl30PimbRI` |
| **SSL Mode** | `require` |

---

### Database Table Schemas

Neon database contains 2 dedicated tables:
- `tally_school_summary` (for School entities)
- `tally_college_summary` (for College / University entities)

#### SQL Table DDL:
```sql
CREATE TABLE IF NOT EXISTS tally_school_summary (
    id SERIAL PRIMARY KEY,
    company_id VARCHAR(50) NOT NULL,
    from_date DATE NOT NULL,
    to_date DATE NOT NULL,
    opening_balance NUMERIC(15, 2) DEFAULT 0.00,
    due_amount NUMERIC(15, 2) DEFAULT 0.00,
    receipt_amount NUMERIC(15, 2) DEFAULT 0.00,
    net_balance NUMERIC(15, 2) DEFAULT 0.00,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_school_summary UNIQUE (company_id, from_date, to_date)
);

CREATE TABLE IF NOT EXISTS tally_college_summary (
    id SERIAL PRIMARY KEY,
    company_id VARCHAR(50) NOT NULL,
    from_date DATE NOT NULL,
    to_date DATE NOT NULL,
    opening_balance NUMERIC(15, 2) DEFAULT 0.00,
    due_amount NUMERIC(15, 2) DEFAULT 0.00,
    receipt_amount NUMERIC(15, 2) DEFAULT 0.00,
    net_balance NUMERIC(15, 2) DEFAULT 0.00,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_college_summary UNIQUE (company_id, from_date, to_date)
);
```

#### SQL UPSERT Query (PostgreSQL):
```sql
INSERT INTO tally_school_summary (
    company_id, from_date, to_date,
    opening_balance, due_amount, receipt_amount, net_balance,
    updated_at
) VALUES (
    '1', '2026-04-01', '2026-09-12',
    14403105.42, 685497604.00, 559939981.02,
    (14403105.42 + 685497604.00 - 559939981.02),
    CURRENT_TIMESTAMP
)
ON CONFLICT (company_id, from_date, to_date)
DO UPDATE SET
    opening_balance = EXCLUDED.opening_balance,
    due_amount = EXCLUDED.due_amount,
    receipt_amount = EXCLUDED.receipt_amount,
    net_balance = EXCLUDED.net_balance,
    updated_at = CURRENT_TIMESTAMP;
```

---

## 4. Code Integration Examples

### A. cURL Command

```bash
curl -X POST "https://dashboard-chi-two-41.vercel.app/api/tally" \
  -H "Content-Type: application/json" \
  -d '{
    "entity": "school",
    "company_id": "1",
    "opening_balance": 14403105.42,
    "due_amount": 685497604.00,
    "receipt_amount": 559939981.02
  }'
```

---

### B. Python 3 Integration Script

```python
import requests

API_URL = "https://dashboard-chi-two-41.vercel.app/api/tally"

def push_tally_summary(company_id, entity, opening, due, receipts):
    payload = {
        "company_id": str(company_id),
        "entity": entity,            # "school" or "college"
        "opening_balance": float(opening),
        "due_amount": float(due),
        "receipt_amount": float(receipts)
    }

    try:
        response = requests.post(
            API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        print(f"Status Code: {response.status_code}")
        print("Response:", response.json())
        return response.status_code == 200
    except Exception as err:
        print(f"Sync failed: {err}")
        return False

# Example invocation:
if __name__ == "__main__":
    push_tally_summary(
        company_id="1",
        entity="school",
        opening=14403105.42,
        due=685497604.00,
        receipts=559939981.02
    )
```

---

### C. Node.js Integration Script

```javascript
const axios = require('axios');

async function syncTallyMetrics() {
  const url = 'https://dashboard-chi-two-41.vercel.app/api/tally';
  
  const payload = {
    entity: 'school',
    company_id: '1',
    from_date: '2026-04-01',
    to_date: '2026-09-12',
    opening_balance: 14403105.42,
    due_amount: 685497604.00,
    receipt_amount: 559939981.02
  };

  try {
    const res = await axios.post(url, payload, {
      headers: { 'Content-Type': 'application/json' }
    });
    console.log('Sync Success:', res.data);
  } catch (err) {
    console.error('Sync Error:', err.response ? err.response.data : err.message);
  }
}

syncTallyMetrics();
```

---

## 5. Summary of Supported Companies

The system maps transactions dynamically based on `company_id` and company codes:
`HHS`, `HJHS`, `SHS`, `METHODIST`, `DPS SITAPUR`, `GDGSILIGURI`, `GDGRUDRAPUR`, `STMARY`, `SSDEC`, `SSPS`, `DEMO`, `SRHU`, `SHOOLINI`, `SRISRI`, `MPGI`, `SILB`, `SHOOLINIONLINE`, `SDDCL`, `SDDIP`, `SDDSCN`, `SDDIET`, `SDDHDC`, `SDDCE`, `SSDCE`, `SDDIMS`, `SDDCTE`.
