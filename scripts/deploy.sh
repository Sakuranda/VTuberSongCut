#!/bin/sh
# The host needs Docker Engine with Compose v2; Python/Node run inside the image.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DOMAIN=${SONGCUT_DOMAIN:-}
RECORDER=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --domain) [ "$#" -ge 2 ] || { echo 'Usage: ./scripts/deploy.sh [--domain songs.example.com] [--recorder]' >&2; exit 2; }; DOMAIN=$2; shift 2 ;;
        --recorder) RECORDER=1; shift ;;
        -h|--help) echo 'Usage: ./scripts/deploy.sh [--domain songs.example.com] [--recorder]'; exit 0 ;;
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
if [ "$RECORDER" -eq 1 ]; then
    if grep -q '^SONGCUT_RECORDER_ENABLED=' .env.server; then
        sed 's/^SONGCUT_RECORDER_ENABLED=.*/SONGCUT_RECORDER_ENABLED=1/' .env.server > .env.server.pending
        mv .env.server.pending .env.server
    else
        printf '\nSONGCUT_RECORDER_ENABLED=1\n' >> .env.server
    fi
fi
append_default() {
    if ! grep -q "^$1=" .env.server; then
        printf '\n%s=%s\n' "$1" "$2" >> .env.server
    fi
}
if grep -q '^SONGCUT_RECORDER_ENABLED=1$' .env.server; then
    recorder_password=$(LC_ALL=C od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    webhook_secret=$(LC_ALL=C od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    append_default SONGCUT_RECORDER_USERNAME recorder
    append_default SONGCUT_RECORDER_PASSWORD "$recorder_password"
    append_default SONGCUT_RECORDER_WEBHOOK_SECRET "$webhook_secret"
fi
set -- docker compose --env-file .env.server -f compose.yaml
if [ -n "$DOMAIN" ]; then
    set -- "$@" -f deploy/compose.domain.yaml
fi
if grep -q '^SONGCUT_RECORDER_ENABLED=1$' .env.server; then
    set -- "$@" -f deploy/compose.recorder.yaml
fi
if [ -f server-accounts.json ]; then
    set -- "$@" -f deploy/compose.accounts.yaml
fi
"$@" up -d --build --wait --wait-timeout 180
if grep -q '^SONGCUT_RECORDER_ENABLED=1$' .env.server; then
    "$@" exec -T songcut python -m studio.recorder --configure-webhook
    printf '录播姬已连接，回调已配置；打开拾歌的录播库添加房间即可。\n'
fi
origin=$(sed -n 's/^SONGCUT_PUBLIC_ORIGIN=//p' .env.server)
printf '\n拾歌已启动：%s\n' "$origin"
if [ "$created" -eq 1 ]; then
    printf '初始用户名：admin\n初始密码：%s\n' "$password"
    printf '账户已保存在权限为 0600 的 .env.server；请保存密码，不要提交或公开此文件。\n'
else
    printf '继续使用 .env.server 中的服务器账户。\n'
fi
