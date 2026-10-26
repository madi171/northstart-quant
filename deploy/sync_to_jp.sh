#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$(cd -- "${script_dir}/.." && pwd)"
remote_host="jp"
remote_dir="services/northstar-quant"

if (( $# > 0 )); then
    printf '用法：%s\n' "$0" >&2
    exit 2
fi

for tool in ssh rsync; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf '缺少依赖：%s，请先安装。\n' "$tool" >&2
        exit 1
    fi
done

printf '同步 %s/ → %s:~/%s/\n' "$source_dir" "$remote_host" "$remote_dir"
if ! ssh -o BatchMode=no -o StrictHostKeyChecking=ask "$remote_host" "mkdir -p -- '${remote_dir}'"; then
    printf 'SSH 连接失败。首次连接请核对主机指纹后确认；无人值守运行前请先手动执行 ssh jp。\n' >&2
    printf '若提示主机密钥发生变化，请先确认服务器身份，不要直接删除 known_hosts 记录。\n' >&2
    exit 1
fi
rsync \
    --archive \
    --compress \
    --human-readable \
    --itemize-changes \
    --protect-args \
    --rsh='ssh -o BatchMode=yes' \
    "${source_dir}/" \
    "${remote_host}:${remote_dir}/"
printf '同步完成。\n'
