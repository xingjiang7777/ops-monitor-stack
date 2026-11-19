#!/usr/bin/env bash
# 故障演练：在受监控的磁盘上写入一个大文件，制造"磁盘将被写满"的趋势
# 用法:
#   bash scripts/simulate_disk_full.sh            # 默认写入 2G 到 /var/tmp/fill-test.bin
#   bash scripts/simulate_disk_full.sh 1G         # 指定大小
#   bash scripts/simulate_disk_full.sh --clean    # 删除演练文件
set -euo pipefail

TARGET="/var/tmp/fill-test.bin"
SIZE="2G"

if [ "${1:-}" = "--clean" ]; then
    if [ -f "$TARGET" ]; then
        rm -f "$TARGET"
        echo "已删除 $TARGET"
    else
        echo "$TARGET 不存在，无需清理"
    fi
    exit 0
fi

if [ -n "${1:-}" ]; then
    SIZE="$1"
fi

avail=$(df -h "$(dirname "$TARGET")" | awk 'NR==2{print $4}')
echo "目标文件: $TARGET"
echo "待写入:   $SIZE"
echo "当前可用: $avail"
echo
echo "注意：这会真实占用磁盘。演练结束后执行 --clean 删除。"
sleep 2

if command -v fallocate >/dev/null 2>&1; then
    fallocate -l "$SIZE" "$TARGET"
else
    # 无 fallocate 时用 dd 写入随机数据，速度慢但不依赖文件系统特性
    dd if=/dev/urandom of="$TARGET" bs=1M count=$(( ${SIZE%G} * 1024 )) status=progress
fi

echo
echo "写入完成: $(du -h "$TARGET" | cut -f1)"
echo
echo "接下来会发生什么："
echo "  1. DiskWillFillIn4Hours 规则依赖最近 6 小时的可用空间斜率；"
echo "     刚写入时窗口内数据不足，预测值会被低估，通常 10-30 分钟后才触发。"
echo "  2. 触发后告警经 Alertmanager 进入中继，飞书群收到消息。"
echo "  3. 查看当前规则状态: curl -s localhost:9090/api/v1/rules | grep -o '\"name\":\"[^\"]*\"'"
echo
echo "演练结束请执行: bash scripts/simulate_disk_full.sh --clean"
