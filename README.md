# Hub Billing Provider

Hub que coleta, agrupa e envia ao sistema de aluguéis as contas recorrentes de energia (CPFL / RGE), água (Aegea / Corsan) e telecom (Vivo).
Expõe API REST e webhooks para sistemas acoplados. Requisitos completos no PO: `docs/po/`.

## Stack
Python 3.12+ · FastAPI · RabbitMQ (aio-pika) · Redis · PostgreSQL · Docker · pytest. Roda em Windows e Ubuntu.

## Estrutura
```
src/hub_billing/
  core/        config, logs JSON, Correlation ID, segurança (Argon2, JWT, cifragem)
  domain/      modelo canônico de conta, agrupamento em lotes, permissões
  adapters/    interface ProviderAdapter + cpfl (CPFL/RGE), aegea (Aegea/Corsan), vivo (Vivo), fake
  messaging/   envelope de mensagens, topologia RabbitMQ (retry 30s/5m/1h + DLQ), cliente aio-pika
  infra/       idempotência no Redis
  webhooks/    assinatura HMAC-SHA256 e entrega
  workers/     lógica dos workers e ponto de entrada (hub-worker <nome>)
  api/         FastAPI: login JWT, admin, usuários e permissões, disparo de coleta
tests/unit/    testes unitários
docs/po/       PO versionado (PDF + infográfico por versão)
```

## Começando
O jeito mais simples, na pasta do projeto:

| | Windows (duplo clique) | Ubuntu |
|---|---|---|
| Preparar (uma vez, ou após atualizar) | `iniciar.bat` | `./iniciar.sh` |
| Rodar a aplicação | `aplicacao.bat` | `./aplicacao.sh` |

`iniciar` cria o ambiente Python, instala as dependências, gera o `.env` com segredos novos, sobe RabbitMQ, Redis e
PostgreSQL no Docker e roda os testes. `aplicacao` sobe a infraestrutura, a API (http://localhost:8000/docs) e os
5 workers. Precisa de Python 3.12+ e Docker Desktop.

## Testes
Os testes unitários rodam sem RabbitMQ, Redis ou PostgreSQL (usam simulações), nos três ambientes:

| Ambiente | Comando |
|---|---|
| Ubuntu | `make install && make test` |
| Windows (PowerShell) | `.\scripts\install.ps1` e depois `.\scripts\test.ps1` |
| Docker (Ubuntu ou Windows) | `make test-docker`, `./scripts/test-docker.sh` ou `.\scripts\test-docker.ps1` |

Todos executam ruff, mypy e pytest com cobertura mínima de 80%. O CI do GitHub roda a mesma bateria em
ubuntu-latest e windows-latest (Python 3.12 e 3.13) e dentro do container.

Ubuntu:
```
make install && make test
make api          # http://localhost:8000/docs
make up           # sobe tudo no Docker
```
Windows (PowerShell):
```
.\scripts\install.ps1
.\scripts\test.ps1
.\scripts\api.ps1
.\scripts\up.ps1  # Docker Desktop
```
Copie `.env.example` para `.env` e troque os segredos. A conta admin é criada no primeiro boot com
`HUB_ADMIN_EMAIL` / `HUB_ADMIN_PASSWORD` e a troca de senha é obrigatória no primeiro acesso.

## Importar uma fatura (coleta disponível hoje)
Enquanto o meio de coleta automática de cada concessionária não é definido (épico E1), as contas entram pelo
upload da fatura em PDF, que serve para CPFL/RGE, Aegea/Corsan e Vivo:
```
curl -H "Authorization: Bearer $TOKEN" -F provider=cpfl -F distributor=rge -F file=@fatura.pdf \
     http://localhost:8000/api/v1/bills/import
```
O hub lê a linha digitável (valida os dígitos verificadores FEBRABAN e tira dela o valor), o vencimento, a competência
e o número da conta. `account_number` e `reference_month` podem ser enviados para corrigir a leitura. Divergências
(valor impresso diferente do código, segmento de outra concessionária) voltam em `warnings`.
`GET /api/v1/barcodes/{linha}` só valida uma linha digitável.

## Coleta automática pelos portais (RGE/CPFL e Corsan/Aegea)
Os portais usam login da Microsoft (B2C) com código de verificação, então o login é feito por uma pessoa e o hub
reaproveita o token. No PC onde o hub roda (precisa de `pip install -e ".[portal]"` e `python -m playwright install chromium`):
```
python tools/login_portal.py cpfl --distributor rge       # abre o portal da RGE; entre normalmente
python tools/login_portal.py aegea --distributor corsan   # idem na Corsan
```
Depois dispare a coleta por `POST /api/v1/collections`. A senha do portal nunca é guardada: só o token, cifrado com
`HUB_ENCRYPTION_KEY` (a mesma na API e nos workers). Detalhes em `docs/descoberta-portais.md`.

## Correlation ID
Envie `X-Correlation-ID` (ou deixe o hub gerar). Ele volta na resposta e acompanha mensagens, logs, envio ao
sistema de aluguéis e webhooks.

## PO versionado
A cada **10 PRs mergeados** na `main`, a GitHub Action `po-release` gera automaticamente um novo PDF e um novo
infográfico em `docs/po/vX.Y.Z/` (versão MINOR, com os títulos dos PRs no changelog), cria a tag `po-vX.Y.Z` e envia.
Versões nunca são sobrescritas. Para conferir quantos PRs faltam ou gerar manualmente:
```
python tools/build_po.py --every-prs 10                                     # mostra quantos PRs faltam
```
```
python tools/build_po.py --bump minor -m "Descrição da alteração" --git    # Ubuntu
.\scripts\po.ps1 --bump minor -m "Descrição da alteração" --git            # Windows
```
`--git` faz commit, cria a tag `po-vX.Y.Z` e envia ao GitHub.

## Fluxo de ponta a ponta
1. `POST /api/v1/bills/import` lê a fatura e publica `collect.result`.
2. Worker **normalizer**: modelo canônico, idempotência no Redis, vínculo conta -> imóvel (`PUT /api/v1/accounts/{provedor}/{conta}`).
3. Worker **grouper**: grava a conta no PostgreSQL. Sem vínculo, ela fica em `MANUAL_REVIEW` e sai o evento `bill.manual_review`;
   ao vincular a conta depois, ela é liberada.
4. `POST /api/v1/batches/close {"reference_month": "2026-10"}` fecha os lotes por imóvel/contrato e publica `batch.ready`.
5. Worker **dispatcher**: envia ao sistema de aluguéis (contrato proposto em `docs/integracao-locacao.md`) e emite `batch.sent`
   ou `batch.failed`. Falhas temporárias usam retry 30 s / 5 min / 1 h e DLQ.

## Estado atual
Pronto: fundação, Correlation ID, domínio, agrupamento, permissões, API de usuários/coleta, topologia de filas,
idempotência, webhooks assinados, importação de fatura em PDF com validação FEBRABAN, persistência (vínculos, contas e
lotes) e envio ao sistema de aluguéis com retry, todos com testes.
Pendente: confirmar a coleta automática da RGE e da Corsan no primeiro login real, coleta da Vivo, contrato definitivo da API do sistema
de aluguéis, cadastro de assinaturas de webhook e migrações de banco (hoje o schema é criado no boot).
