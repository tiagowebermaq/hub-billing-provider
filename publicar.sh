#!/usr/bin/env bash
# Hub Billing Provider - publica (ou republica) o hub no servidor, em https://billing.heimber.com.br
# Rode na pasta do hub, na VM do omnichannel:  ./publicar.sh
# Pode rodar quantas vezes quiser: não apaga dados nem troca senhas já criadas.
set -euo pipefail
cd "$(dirname "$0")"
DOMINIO="${HUB_DOMINIO:-billing.heimber.com.br}"
compose() { docker compose -f docker-compose.yml -f deploy/docker-compose.servidor.yml "$@"; }
segredo() { head -c 32 /dev/urandom | base64 | tr -d '=+/\n' | head -c 40; }
fernet()  { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n'; }
define()  { # define VAR=valor no .env só se estiver vazia ou com o valor de exemplo
  local var="$1" valor="$2" atual
  atual="$(grep -E "^$var=" .env | tail -1 | cut -d= -f2- || true)"
  case "$atual" in ""|troque-*|hub|admin)
    if grep -qE "^$var=" .env; then sed -i "s|^$var=.*|$var=$valor|" .env; else echo "$var=$valor" >> .env; fi
    echo "   $var criado";;
  esac
}

command -v docker >/dev/null || { echo "[ERRO] Instale o Docker: https://docs.docker.com/engine/install/"; exit 1; }
docker compose version >/dev/null || { echo "[ERRO] Falta o Docker Compose (plugin docker compose)."; exit 1; }

echo "== 1/5 Configuração (.env)"
if [ ! -f .env ]; then
  if [ -f migracao/env-do-pc.txt ]; then cp migracao/env-do-pc.txt .env; echo "   .env copiado do PC (mesmas chaves: sessões e senhas cifradas continuam valendo)"
  else cp .env.example .env; fi
fi
sed -i 's/\r$//' .env
define HUB_JWT_SECRET "$(segredo)"
define HUB_ENCRYPTION_KEY "$(fernet)"
define HUB_DB_PASSWORD "$(segredo)"
define HUB_RABBITMQ_PASSWORD "$(segredo)"
define HUB_REDIS_PASSWORD "$(segredo)"
grep -qE "^HUB_DOMINIO=" .env || echo "HUB_DOMINIO=$DOMINIO" >> .env
chmod 600 .env
mkdir -p logs && chmod 777 logs

echo "== 2/5 Construindo e subindo banco, RabbitMQ e Redis"
compose build -q api
compose up -d postgres rabbitmq redis --wait

echo "== 3/5 Dados do PC"
if [ -f migracao/hub-dados.sql ] && [ ! -f migracao/.importado ]; then
  tabelas="$(compose exec -T postgres psql -U hub -d hub -tAc "select count(*) from information_schema.tables where table_schema='public'")"
  if [ "${tabelas// /}" = "0" ]; then
    compose exec -T postgres psql -q -U hub -d hub -v ON_ERROR_STOP=1 < migracao/hub-dados.sql
    touch migracao/.importado; echo "   dados do PC importados"
  else echo "   o banco do servidor já tem dados: não importei por cima (apague migracao/hub-dados.sql para não ver esta mensagem)"; fi
else echo "   nada para importar"; fi

echo "== 4/5 API e os 7 workers"
compose up -d --remove-orphans

echo "== 5/5 Endereço https://$DOMINIO"
porta_ocupada() { ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q . ; }
if compose ps --services --status running | grep -qx caddy || { ! porta_ocupada 80 && ! porta_ocupada 443; }; then
  compose --profile caddy up -d caddy
  echo "   Caddy atendendo 80/443 e cuidando do HTTPS sozinho."
elif command -v nginx >/dev/null; then
  echo "   As portas 80/443 já são do nginx desta VM (omnichannel). Para ligar o domínio, rode uma vez:"
  echo "     sudo cp deploy/nginx-billing.conf /etc/nginx/sites-available/$DOMINIO"
  echo "     sudo ln -sf /etc/nginx/sites-available/$DOMINIO /etc/nginx/sites-enabled/"
  echo "     sudo nginx -t && sudo systemctl reload nginx"
  echo "     sudo certbot --nginx -d $DOMINIO"
else
  echo "   As portas 80/443 já estão em uso por outro programa. Aponte $DOMINIO para http://127.0.0.1:${HUB_PORT:-8010}"
  echo "   no proxy que atende essa VM (veja docs/publicar-servidor.md)."
fi
echo
echo "Pronto. Painel: https://$DOMINIO/painel  (na própria VM: http://127.0.0.1:${HUB_PORT:-8010}/painel)"
echo "Para atualizar depois de um merge: ./atualizar.sh"
