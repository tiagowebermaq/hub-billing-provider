# Publicar o hub no servidor (VM do omnichannel, R610)

Endereço final: **https://billing.heimber.com.br/painel**

No servidor o hub roda inteiro no Docker: API, os 7 workers, PostgreSQL, RabbitMQ e Redis. Não fica nenhuma
janela aberta e tudo volta sozinho se a VM reiniciar. Banco, RabbitMQ e Redis ficam fechados, com senhas fortes
criadas na primeira publicação. A API só escuta em `127.0.0.1:8010`; a internet chega por um proxy com HTTPS.

## 1. DNS (uma vez)
No painel de DNS do `heimber.com.br`, crie um registro **A**: nome `billing`, valor = IP público da VM do
omnichannel (o mesmo que o domínio do omnichannel usa). Se o roteador faz NAT, as portas 80 e 443 já devem estar
apontando para essa VM, porque o omnichannel usa as mesmas.

## 2. Levar os dados do PC (uma vez)
No PC, na pasta `C:\Repositorio\HUB BILLING`, dê dois cliques em **exportar-dados.bat**. Ele cria a pasta
`migracao` com o banco (faturas, contas, usuários, sessões dos portais) e uma cópia do `.env` (as chaves que abrem
as senhas cifradas). A pasta tem senhas: leve-a ao servidor (WinSCP, por exemplo) e depois apague a cópia.

## 3. Publicar (na VM)
```bash
git clone https://github.com/tiagowebermaq/hub-billing-provider.git
cd hub-billing-provider
# copie a pasta migracao do PC para cá
./publicar.sh
```
O `publicar.sh` cria as senhas que faltam no `.env`, sobe tudo, importa os dados do PC (só se o banco do servidor
estiver vazio) e cuida do endereço:
- **portas 80/443 livres**: sobe o Caddy, que pega o certificado HTTPS sozinho;
- **portas 80/443 com o nginx do omnichannel**: mostra os 4 comandos para ligar o domínio no nginx
  (`deploy/nginx-billing.conf`) e pegar o certificado com o certbot;
- **outro proxy** (Traefik, IIS, Nginx Proxy Manager): aponte `billing.heimber.com.br` para `http://127.0.0.1:8010`.

Depois de cada merge: `./atualizar.sh` (baixa do GitHub e republica). Para parar: `./parar-servidor.sh`.
Logs dos workers: pasta `logs/` do hub.

## Pede atenção
- **Conectar RGE/Corsan**: o botão abre um navegador na máquina onde o hub roda. No servidor isso não funciona;
  até a mudança "logar no PC e enviar a sessão ao servidor", conecte os portais no PC antes de exportar os dados.
- Precisa de Docker com Compose 2.24 ou mais novo (`docker compose version`).
