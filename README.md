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
  adapters/    interface ProviderAdapter + cpfl (CPFL/RGE), aegea (Aegea/Corsan), vivo (Vivo), fake;
               email_inbox (faturas que chegam por e-mail, via IMAP)
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
| Parar a aplicação | `parar.bat` | `./parar.sh` (ou Ctrl+C) |
| Esqueci a senha do painel | `resetar-senha.bat` | `.venv/bin/python tools/reset_admin.py` |

O `aplicacao.bat` abre o painel no navegador padrão. Só os logins dos portais usam o navegador do hub (o Chromium
instalado pelo `iniciar.bat`): os logins da RGE e da Corsan são feitos pelo botão **Conectar** da aba Portais,
que abre o portal numa janela com a faixa verde "Janela do Hub Billing"; um portal aberto no Chrome comum não
conecta o hub.

`iniciar` cria o ambiente Python, instala as dependências, gera o `.env` com segredos novos, sobe RabbitMQ, Redis e
PostgreSQL no Docker e roda os testes. `aplicacao` sobe a infraestrutura, a API e os
7 workers e abre o painel de administração em http://localhost:8010/painel (faturas, importação de PDF, contas e
imóveis, lotes, sessões dos portais e coleta). A documentação da API fica em http://localhost:8010/docs. Precisa de
Python 3.12+ e Docker Desktop. No Windows, se o Docker Desktop estiver fechado (por exemplo, depois de ligar o
PC), `iniciar`, `aplicacao` e `resetar-senha` abrem o Docker Desktop sozinhos e esperam ele ficar pronto.

O hub usa a porta 8010 para não conflitar com outros projetos na 8000. Para usar outra, defina `HUB_PORT`
antes de rodar `aplicacao` (ex.: `set HUB_PORT=8020` no Windows). Se a porta estiver ocupada, o script avisa e para.

## Servidor (https://billing.heimber.com.br)
Na VM do omnichannel, no R610: `./publicar.sh` sobe tudo no Docker com HTTPS; `exportar-dados.bat` no PC leva os dados. Passo a passo em [docs/publicar-servidor.md](docs/publicar-servidor.md).

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
make api          # http://localhost:8010/docs
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
`HUB_ADMIN_EMAIL` / `HUB_ADMIN_PASSWORD` (padrão `admin@hub.local` / `troque-no-primeiro-acesso`) e o painel
pede a troca de senha no primeiro acesso. Até essa troca, editar o `.env` e reiniciar atualiza o login do admin;
depois dela vale a senha salva. Usuários, senhas e permissões ficam no banco (PostgreSQL) e sobrevivem a reinícios.

## Importar uma fatura (coleta disponível hoje)
Enquanto o meio de coleta automática de cada concessionária não é definido (épico E1), as contas entram pelo
upload da fatura em PDF, que serve para CPFL/RGE, Aegea/Corsan e Vivo:
```
curl -H "Authorization: Bearer $TOKEN" -F provider=cpfl -F distributor=rge -F file=@fatura.pdf \
     http://localhost:8010/api/v1/bills/import
```
O hub lê a linha digitável (valida os dígitos verificadores FEBRABAN e tira dela o valor), o vencimento, a competência
e o número da conta. `account_number` e `reference_month` podem ser enviados para corrigir a leitura. Divergências
(valor impresso diferente do código, segmento de outra concessionária) voltam em `warnings`.
`GET /api/v1/barcodes/{linha}` só valida uma linha digitável.

## Coleta automática pelos portais (RGE/CPFL e Corsan/Aegea)
Os portais usam login da Microsoft (B2C) com código de verificação, então o login é feito por uma pessoa e o hub
reaproveita o token. No painel, aba **Portais**, clique em **Conectar RGE** ou **Conectar Corsan**: o portal abre numa
janela do navegador no PC onde o hub roda, você entra normalmente e a sessão é salva sozinha (o `iniciar.bat` já
instala o navegador). Pelo terminal também dá: `python tools/login_portal.py cpfl --distributor rge` (ou `aegea --distributor corsan`).
Depois dispare a coleta na mesma aba ou por `POST /api/v1/collections`. A senha do portal nunca é guardada: só o token, cifrado com
`HUB_ENCRYPTION_KEY` (a mesma na API e nos workers). Detalhes em `docs/descoberta-portais.md`.

## Faturas por e-mail (caixa 41u6u31@heimber.com.br)
As concessionárias mandam a fatura por e-mail (ou alguém encaminha) para a caixa do hub. No painel, aba **E-mail**,
confira o servidor (já vem POP3 `mail.webermaq.com`, porta 995, SSL, apagando do servidor 10 dias depois de lido;
IMAP também é aceito), informe a senha da caixa (ficam cifrados no banco; sem cadastro lá, vale o `.env`,
variáveis `HUB_EMAIL_...`) e clique em **Testar conexão**. Se as faturas vierem em PDF com senha (ex.: início do CPF),
cadastre as senhas no mesmo formulário.

- Worker **mailbox**: a cada 5 min (ou ao clicar **Verificar agora**, fila `email.poll`) baixa os e-mails novos (no POP3 o
  hub guarda quais já leu; os que já estavam na caixa ao ligar são lidos, mas nunca apagados),
  guarda cada um (o Message-ID impede duplicar) e publica `email.received` com um Correlation ID novo. Só marca como
  lido depois que o e-mail está guardado e na fila.
- Worker **mailreader**: lê os PDFs com o adapter de e-mail (`adapters/email_inbox`), identifica RGE/CPFL, Corsan/Aegea
  ou Vivo pelo texto da fatura, assunto e remetente, e publica um `collect.result` por fatura, que segue o fluxo normal.
  O PDF fica disponível na coluna Pagar da aba Faturas.
- Auto-cura: caixa fora do ar ou senha recusada espera 30 s, 1 min, 2 min... até 30 min e tenta de novo, sem derrubar o
  worker; e-mail parado na fila por mais de 15 min é republicado; erro inesperado na leitura vai para retry
  (30 s / 5 min / 1 h) e depois para a DLQ `email.received.dlq`. Fatura ilegível fica como **Revisar** no painel (e sai o
  evento `email.manual_review`), com o botão **Reprocessar**.

## Contas pagas e em aberto
Na aba **Faturas**, a coluna **Pagamento** mostra **Em aberto** (ainda no portal, dentro do prazo), **Vencida** (em
aberto depois do vencimento, em vermelho) ou **Paga**. A cada coleta, o hub confere as contas gravadas de cada UC/matrícula
que o portal listou: a que sumiu das contas em aberto vira **Paga**, com a data em que o hub notou (e sai o evento
`bill.paid`); se voltar a aparecer em aberto, volta (evento `bill.reopened`). Contas de UCs que o portal não acompanha
(vindas só por e-mail ou importação) não mudam sozinhas. O site novo da CPFL/RGE mostra uma conta só por UC: ali só viram
pagas as competências anteriores à mostrada, e nenhuma enquanto a mostrada estiver atrasada. O botão **Marcar como paga**
(ou **Reabrir**) decide à mão, e a coleta não muda mais essa conta. Filtro por situação: `GET /api/v1/bills?payment=PAGA`.

## Webhooks para os sistemas vizinhos
Cada conta gravada (de portal, e-mail ou importação) gera o evento `bill.stored`, com o resumo da conta e o link
`/api/v1/bills/{id}`, onde o sistema vizinho busca os dados completos (login com um usuário do hub, perfil Consulta).
No painel, aba **Webhooks** (admin), cadastre o nome e a URL de cada sistema e os eventos que ele quer receber
(`bill.stored`, `bill.manual_review`, `bill.paid`, `bill.reopened`, `batch.sent`, `batch.failed`, `collect.login_required`, `email.manual_review`).
O segredo aparece uma vez no cadastro: o sistema confere a assinatura `X-Hub-Signature-256` (HMAC-SHA256 de
`timestamp.corpo`, com `X-Hub-Timestamp`) e descarta repetidos pelo `X-Hub-Delivery`. Cada entrega tem fila própria
(`webhook.delivery`) com retry 30 s / 5 min / 1 h e DLQ, então um sistema fora do ar não atrasa os outros. O botão
**Testar** manda um `webhook.ping`; o painel mostra as últimas entregas.

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
de aluguéis, e migrações de banco (hoje o schema é criado no boot).
