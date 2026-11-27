#!/usr/bin/env bash
# Hub Billing Provider - baixa a versão nova do GitHub e republica no servidor. Dados e senhas ficam como estão.
set -euo pipefail
cd "$(dirname "$0")"
git pull --ff-only
./publicar.sh
