# Changelog do PO Hub Billing Provider

Formato: `## [versão] - AAAA-MM-DD - autor`, seguido dos itens alterados.
Política: SemVer (MAJOR = visão/escopo/arquitetura, MINOR = novo requisito/provedor/épico, PATCH = correções de texto).

## [1.6.0] - 2026-10-29 - Weber
- Versão automática após 13 PRs mergeados.
- PR #1: Adapter Vivo, novo PO a cada 10 PRs e PO v1.2.0
- PR #2: Importação de fatura em PDF e validação da linha digitável
- PR #3: Persistência em PostgreSQL: vínculos conta-imóvel, contas e lotes
- PR #4: Envio dos lotes ao sistema de aluguéis e workers de ponta a ponta
- PR #5: Coleta automática da RGE e da Corsan pelas APIs dos portais
- PR #6: Arquivos iniciar e aplicacao para Windows e Ubuntu
- PR #7: Painel de administração web em /painel
- PR #8: Cadastro do imóvel e do titular nas contas
- PR #9: Painel no estilo verde-petróleo, claro e escuro
- PR #10: Hub passa a usar a porta 8010 e avisa se a porta estiver ocupada
- PR #11: Usuários no banco e troca de senha obrigatória no painel
- PR #12: parar.bat e parar.sh para fechar o hub
- PR #13: iniciar.bat limpa sobras de instalações interrompidas

## [1.5.0] - 2026-10-29 - Weber
- Versão automática após 12 PRs mergeados.
- PR #1: Adapter Vivo, novo PO a cada 10 PRs e PO v1.2.0
- PR #2: Importação de fatura em PDF e validação da linha digitável
- PR #3: Persistência em PostgreSQL: vínculos conta-imóvel, contas e lotes
- PR #4: Envio dos lotes ao sistema de aluguéis e workers de ponta a ponta
- PR #5: Coleta automática da RGE e da Corsan pelas APIs dos portais
- PR #6: Arquivos iniciar e aplicacao para Windows e Ubuntu
- PR #7: Painel de administração web em /painel
- PR #8: Cadastro do imóvel e do titular nas contas
- PR #9: Painel no estilo verde-petróleo, claro e escuro
- PR #10: Hub passa a usar a porta 8010 e avisa se a porta estiver ocupada
- PR #11: Usuários no banco e troca de senha obrigatória no painel
- PR #12: parar.bat e parar.sh para fechar o hub

## [1.4.0] - 2026-10-29 - Weber
- Versão automática após 11 PRs mergeados.
- PR #1: Adapter Vivo, novo PO a cada 10 PRs e PO v1.2.0
- PR #2: Importação de fatura em PDF e validação da linha digitável
- PR #3: Persistência em PostgreSQL: vínculos conta-imóvel, contas e lotes
- PR #4: Envio dos lotes ao sistema de aluguéis e workers de ponta a ponta
- PR #5: Coleta automática da RGE e da Corsan pelas APIs dos portais
- PR #6: Arquivos iniciar e aplicacao para Windows e Ubuntu
- PR #7: Painel de administração web em /painel
- PR #8: Cadastro do imóvel e do titular nas contas
- PR #9: Painel no estilo verde-petróleo, claro e escuro
- PR #10: Hub passa a usar a porta 8010 e avisa se a porta estiver ocupada
- PR #11: Usuários no banco e troca de senha obrigatória no painel

## [1.3.0] - 2026-10-29 - Weber
- Versão automática após 10 PRs mergeados.
- PR #1: Adapter Vivo, novo PO a cada 10 PRs e PO v1.2.0
- PR #2: Importação de fatura em PDF e validação da linha digitável
- PR #3: Persistência em PostgreSQL: vínculos conta-imóvel, contas e lotes
- PR #4: Envio dos lotes ao sistema de aluguéis e workers de ponta a ponta
- PR #5: Coleta automática da RGE e da Corsan pelas APIs dos portais
- PR #6: Arquivos iniciar e aplicacao para Windows e Ubuntu
- PR #7: Painel de administração web em /painel
- PR #8: Cadastro do imóvel e do titular nas contas
- PR #9: Painel no estilo verde-petróleo, claro e escuro
- PR #10: Hub passa a usar a porta 8010 e avisa se a porta estiver ocupada

## [1.2.0] - 2026-10-28 - Weber
- Inclui a Vivo (telecom) como fornecedor de contas de consumo, com adapter vivo e tipo TELECOM.
- Regra de versionamento: novo PO (PDF + infográfico) a cada 10 PRs mergeados na main, gerado pela GitHub Action po-release.
- Testes ampliados para 63 (adapter Vivo e contagem de PRs).

## [1.1.0] - 2026-10-25 - Weber
- Testes unitários ampliados (59 testes, cobertura 98%), incluindo RabbitMQ simulado, logs, usuários e gerador do PO.
- Testes executáveis em Docker (estágio test e serviço tests no Compose), Ubuntu e Windows, com scripts para cada ambiente.
- CI com testes no container, além da matriz ubuntu-latest e windows-latest.

## [1.0.0] - 2026-10-25 - Weber
- Versão inicial do PO: visão, escopo, atores, provedores (CPFL/RGE, Aegea/Corsan), requisitos funcionais e não funcionais.
- Arquitetura com Python, RabbitMQ, Redis, PostgreSQL, Docker, adapters por provedor e Correlation ID.
- API REST, webhooks assinados (HMAC), conta admin e permissões por usuário.
- Backlog em 11 épicos, riscos, questões em aberto e política de versionamento (PDF + infográfico a cada alteração).
