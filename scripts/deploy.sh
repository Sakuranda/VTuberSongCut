#!/bin/sh
# The host needs Docker Engine with Compose v2; Python/Node run inside the image.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DOMAIN=${SONGCUT_DOMAIN:-}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --domain) [ "$#" -ge 2 ] || { echo 'Usage: ./scripts/deploy.sh [--domain songs.example.com]' >&2; exit 2; }; DOMAIN=$2; shift 2 ;;
        -h|--help) echo 'Usage: ./scripts/deploy.sh [--domain songs.example.com]'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
if [ -n "$DOMAIN" ]; then
    case "$DOMAIN" in
        *[!a-zA-Z0-9.-]*|.*|*..*|*.) echo 'Use a DNS hostname without protocol, path, or port.' >&2; exit 2 ;;
    esac
fi
docker compose version >/dev/null
docker info >/dev/null
umask 077
created=0
if [ ! -f .env.server ]; then
    password=$(LC_ALL=C od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    port=${SONGCUT_HTTP_PORT:-8765}
    origin="http://localhost:$port"
    [ -z "$DOMAIN" ] || origin="https://$DOMAIN"
    cat > .env.server <<ENV
SONGCUT_SERVER_USERNAME=admin
SONGCUT_SERVER_PASSWORD=$password
SONGCUT_PUBLIC_ORIGIN=$origin
SONGCUT_BIND=127.0.0.1
SONGCUT_HTTP_PORT=$port
SONGCUT_DOMAIN=$DOMAIN
ENV
    created=1
elif [ -n "$DOMAIN" ]; then
    # Preserve the existing password and workspace when adding/changing a domain.
    sed -e "s|^SONGCUT_PUBLIC_ORIGIN=.*|SONGCUT_PUBLIC_ORIGIN=https://$DOMAIN|" \
        -e "s|^SONGCUT_DOMAIN=.*|SONGCUT_DOMAIN=$DOMAIN|" .env.server > .env.server.pending
    mv .env.server.pending .env.server
fi
chmod 600 .env.server
DOMAIN=$(sed -n 's/^SONGCUT_DOMAIN=//p' .env.server)
export SONGCUT_DOMAIN="$DOMAIN"
if [ -n "$DOMAIN" ]; then
    docker compose --env-file .env.server -f compose.yaml -f deploy/compose.domain.yaml up -d --build --wait --wait-timeout 180
else
    docker compose --env-file .env.server -f compose.yaml up -d --build --wait --wait-timeout 180
fi
origin=$(sed -n 's/^SONGCUT_PUBLIC_ORIGIN=//p' .env.server)
printf '\n拾歌已启动：%s\n' "$origin"
if [ "$created" -eq 1 ]; then
    printf '初始用户名：admin\n初始密码：%s\n' "$password"
    printf '账户已保存在权限为 0600 的 .env.server；请保存密码，不要提交或公开此文件。\n'
else
    printf '继续使用 .env.server 中的服务器账户。\n'
fi
