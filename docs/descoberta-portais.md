# Descoberta dos portais RGE (CPFL) e Corsan (Aegea)

Data: 2026-10-28. Fonte: navegação **sem login** nas páginas públicas e leitura dos bundles JavaScript dos próprios
portais. Os formatos das respostas autenticadas vêm do código do front-end e ainda precisam ser confirmados com o
primeiro login real. Chaves e tokens foram omitidos.

## RGE (Agência Virtual CPFL) — adapter `cpfl`, distribuidora `rge`
- Front-end: `https://servicosonline.cpfl.com.br/agencia-webapp/` (AngularJS). `rge-rs.com.br` redireciona para lá.
- API: `https://servicosonline.cpfl.com.br/agencia-webapi/api`. Cloudflare (bot management) na frente.
- Login: `www.cpfl.com.br/b2c-auth/login?sistema=agv` -> Azure AD B2C `cpflb2cprd.b2clogin.com`, política
  `B2C_1A_SIGNUP_SIGNIN_MFA_FRONT` (MFA). Depois o front chama `POST /api/token` (form) e guarda a resposta inteira em
  `sessionStorage["userSessionStorage"]`: `access_token`, `refresh_token`, `Instalacao` e `ListaInstalacoes` (strings JSON).
- Renovação: `POST /api/token` com `client_id=agencia-virtual-cpfl-web`, `grant_type=refresh_token`, `refresh_token`.

| Método | Caminho | Corpo / parâmetros | Resposta |
|---|---|---|---|
| POST | `historico-contas/validar-situacao` | `RetornarDetalhes, CodigoFase, IndGrupoA, Situacao, ContaContrato, CodigoClasse, CodEmpresaSAP, Instalacao, ParceiroNegocio, GerarProtocolo` | `ContasAberto[]`: `MesReferencia` (MM/AAAA), `Vencimento`, `Valor` ("123,45"), `CodigoBarras`, `NumeroContaEnergia`, `strEstaticoPIX`, `Status` |
| POST | `historico-contas/contas-quitadas` | mesmo corpo | `ContasPagas[]` |
| GET | `historico-contas/conta-completa-pdf` | `numeroContaEnergia, codigoClasse, codEmpresaSAP, instalacao, parceiroNegocio, token, contaAcumulada` | PDF |
| POST | `historico-contas/conta-agrupada` | `Instalacao, ParceiroNegocio` | `ListContaAgrupada[]` |
| GET | `historico-contas/conta-agrupada-pdf` | `numeroContaEnergia, codigoClasse, codEmpresaSAP, codigoTipoParceiro, instalacao, parceiro, parceiroNegocio, token` | PDF |
| POST | `historico-contas/download-xml-nf3e`, `existe-xml-nf3e` | — | XML da NF3e |

## Corsan (Agência Virtual Aegea) — adapter `aegea`, distribuidora `corsan`
- Front-end: `https://cliente.corsan.com.br/` (Angular + MSAL). API: `https://api.aegea.com.br/external/agencia-virtual/app/v1`.
- Cabeçalhos: `Authorization: Bearer`, `Ocp-Apim-Subscription-Key` (chave pública do bundle, vai em `HUB_AEGEA_APIM_KEY`),
  `X-TenantID` (`unidade.tenantIDCorporativo` da matrícula).
- Login: Azure AD B2C `aeclientes.b2clogin.com`, política `B2C_1A_SIGNUP_SIGNIN`, client_id do SPA
  `b673d12b-6c65-45b4-9c3c-299cc574b4f8`, escopo `AguasAPI.ReadWrite`. MSAL guarda os tokens no `localStorage`.
  O refresh token de SPA vale ~24 h. reCAPTCHA v3 só nos endpoints deslogados.

| Método | Caminho | Parâmetros | Resposta |
|---|---|---|---|
| GET | `cliente/matriculas` | — | `content.ligacoes[]`: `numeroLigacao`, `numeroSequencialResponsavel`, `zonaLigacao`, `unidade.tenantIDCorporativo` |
| GET | `fatura/debito-totais/matricula` | `matricula, sequencialResponsavel, zonaLigacao` | `content.debitos[]`: `referencia`, `dataVencimento`, `valorFatura`, `statusFatura`, `codigoBarrasDigitavel`, `codigoPIX`, `consumo`, `anoLancamento`, `numeroAviso`, `zonaLigacao` |
| GET | `fatura/historico-faturas/{matricula}/{seq}/{status}` | `pagina, tamanho`; status `TODAS`, `Paga`, `Em Aberto`, `Atrasada`, `EM_DEBITO` | `content.content[]` |
| GET | `fatura/segunda-via/download` | `matricula, zonaLigacao, numeroAviso, anoLancamento, mesLancamento` | `content.bytes` (PDF base64) |
| GET | `central-faturas/resumo`, `central-faturas/{idMatricula}` | — | matrículas e débitos |
| POST | `central-faturas/{idMatricula}/download-lote` | `[{anoLancamento, mesLancamento}]` | `content.bytes` (ZIP base64) |

## Como o hub usa isso
1. `python tools/login_portal.py cpfl --distributor rge` (ou `aegea --distributor corsan`) abre o portal no PC.
   A pessoa entra com usuário, senha e código; o script captura só o token e o envia ao hub, que guarda cifrado.
2. `POST /api/v1/collections {"provider": "cpfl", "distributor": "rge", "reference_month": "2026-10"}` dispara a coleta.
   O worker collector renova o token, lista as instalações/matrículas e publica um `collect.result` por conta.
3. Sessão vencida gera o evento `collect.login_required` (webhook) em vez de erro.

## A confirmar no primeiro login real
- Se a CPFL pede o código de MFA e quanto tempo dura o refresh token.
- Se o Cloudflare aceita as chamadas fora do navegador com os cookies capturados.
- Se um token da CPFL vale para todas as instalações de `ListaInstalacoes` ou só para a escolhida.
- Os formatos exatos de data e valor nas respostas (o hub já aceita `dd/mm/aaaa`, ISO, número e "1.234,56").
