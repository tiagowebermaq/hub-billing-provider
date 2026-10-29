# Integração com o sistema de aluguéis (proposta)

Contrato que o hub usa hoje para entregar os lotes. **É uma proposta**: quando o sistema de aluguéis tiver a API
definida, o cliente `src/hub_billing/rental/client.py` é ajustado a ela (só esse arquivo muda).

## Requisição
`POST {HUB_RENTAL_API_URL}` (padrão `http://localhost:9000/api/bills`)

Cabeçalhos:

| Cabeçalho | Conteúdo |
|---|---|
| `Content-Type` | `application/json` |
| `Idempotency-Key` | id do lote; o mesmo lote reenviado tem a mesma chave |
| `X-Correlation-ID` | rastreio ponta a ponta (coleta, filas, logs, webhooks) |
| `X-Hub-Timestamp` / `X-Hub-Signature-256` | HMAC-SHA256 de `"{timestamp}." + corpo`, quando `HUB_RENTAL_API_SECRET` está definido |

Corpo: um lote por imóvel + contrato + competência.
```json
{
  "batch_id": "9f1c…",
  "property_id": "ap-101",
  "contract_id": "c-7",
  "reference_month": "2026-10",
  "total": "329.90",
  "bills": [
    {"bill_id": "…", "provider": "cpfl", "distributor": "rge", "utility_type": "ENERGY", "account_number": "40012345",
     "due_date": "2026-11-20", "amount": "150.00", "consumption": "210", "consumption_unit": "kWh",
     "barcode": "8366…", "digitable_line": "83660000001-5 …", "pix_code": null, "document_url": null}
  ]
}
```

## Respostas esperadas

| Resposta | O que o hub faz |
|---|---|
| 2xx | lote aceito (`ACKED`); o corpo JSON volta no evento `batch.sent` |
| 409 | lote já recebido antes: tratado como aceito |
| 429, 5xx, timeout, erro de rede | nova tentativa em 30 s, 5 min e 1 h; depois vai para a DLQ |
| outros 4xx | recusa definitiva: lote `FAILED`, evento `batch.failed`, reenvio manual por `POST /api/v1/batches/{id}/resend` |

## A confirmar com o sistema de aluguéis
1. URL, autenticação (token, mTLS ou HMAC) e ambientes de homologação e produção.
2. Como ele identifica imóvel e contrato (os mesmos ids cadastrados em `PUT /api/v1/accounts/...`?).
3. Se precisa do PDF da fatura (envio do arquivo ou só o link).
4. Se a resposta é síncrona ou se ele confirma depois por webhook.
