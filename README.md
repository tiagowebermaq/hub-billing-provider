# Hub Billing Provider

Hub que coleta, agrupa e envia ao sistema de aluguéis as contas recorrentes de energia (CPFL / RGE) e água (Aegea / Corsan).
Expõe API REST e webhooks para sistemas acoplados. Requisitos completos no PO: `docs/po/`.

## Stack
Python 3.12+ · FastAPI · RabbitMQ (aio-pika) · Redis · PostgreSQL · Docker · pytest. Roda em Windows e Ubuntu.

## Estrutura
```
src/hub_billing/
  core/        config, logs JSON, Correlation ID, segurança (Argon2, JWT, cifragem)
  domain/      modelo canônico de conta, agrupamento em lotes, permissões
  adapters/    interface ProviderAdapter + cpfl (CPFL/RGE), aegea (Aegea/Corsan), fake
  messaging/   envelope de mensagens, topologia RabbitMQ (retry 30s/5m/1h + DLQ), cliente aio-pika
  infra/       idempotência no Redis
  webhooks/    assinatura HMAC-SHA256 e entrega
  workers/     lógica dos workers e ponto de entrada (hub-worker <nome>)
  api/         FastAPI: login JWT, admin, usuários e permissões, disparo de coleta
tests/unit/    testes unitários
docs/po/       PO versionado (PDF + infográfico por versão)
```

## Começando
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

## Correlation ID
Envie `X-Correlation-ID` (ou deixe o hub gerar). Ele volta na resposta e acompanha mensagens, logs, envio ao
sistema de aluguéis e webhooks.

## PO versionado
Toda alteração gera um novo PDF e um novo infográfico em `docs/po/vX.Y.Z/` (SemVer, nunca sobrescreve):
```
python tools/build_po.py --bump minor -m "Descrição da alteração" --git    # Ubuntu
.\scripts\po.ps1 --bump minor -m "Descrição da alteração" --git            # Windows
```
`--git` faz commit, cria a tag `po-vX.Y.Z` e envia ao GitHub.

## Estado atual (sprint 1)
Pronto: fundação, Correlation ID, domínio, agrupamento, permissões, API de usuários/coleta, topologia de filas,
idempotência e webhooks assinados, com testes. Pendente: meio de coleta real da CPFL/RGE e Aegea/Corsan (épico E1),
persistência em PostgreSQL (E2/E3) e envio ao sistema de aluguéis (E7, depende do contrato da API dele).
