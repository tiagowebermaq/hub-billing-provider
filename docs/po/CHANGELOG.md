# Changelog do PO Hub Billing Provider

Formato: `## [versão] - AAAA-MM-DD - autor`, seguido dos itens alterados.
Política: SemVer (MAJOR = visão/escopo/arquitetura, MINOR = novo requisito/provedor/épico, PATCH = correções de texto).

## [1.1.0] - 2026-10-25 - Weber
- Testes unitários ampliados (59 testes, cobertura 98%), incluindo RabbitMQ simulado, logs, usuários e gerador do PO.
- Testes executáveis em Docker (estágio test e serviço tests no Compose), Ubuntu e Windows, com scripts para cada ambiente.
- CI com testes no container, além da matriz ubuntu-latest e windows-latest.

## [1.0.0] - 2026-10-25 - Weber
- Versão inicial do PO: visão, escopo, atores, provedores (CPFL/RGE, Aegea/Corsan), requisitos funcionais e não funcionais.
- Arquitetura com Python, RabbitMQ, Redis, PostgreSQL, Docker, adapters por provedor e Correlation ID.
- API REST, webhooks assinados (HMAC), conta admin e permissões por usuário.
- Backlog em 11 épicos, riscos, questões em aberto e política de versionamento (PDF + infográfico a cada alteração).
