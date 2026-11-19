#!/usr/bin/env bash
# 逐项检查各组件是否就绪，全部通过时退出码为 0
set -uo pipefail

FAIL=0

check() {
    name=$1
    url=$2
    expect=${3:-200}
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)
    code=${code:-000}
    if [ "$code" = "$expect" ]; then
        printf "  [ OK ] %-14s %s -> %s\n" "$name" "$url" "$code"
    else
        printf "  [FAIL] %-14s %s -> %s（期望 %s）\n" "$name" "$url" "$code" "$expect"
        FAIL=1
    fi
}

body_check() {
    name=$1
    url=$2
    expect=$3
    body=$(curl -s --max-time 5 "$url" 2>/dev/null)
    if [ "$body" = "$expect" ]; then
        printf "  [ OK ] %-14s %s -> %s\n" "$name" "$url" "$body"
    else
        printf "  [FAIL] %-14s %s -> 内容为 '%s'（期望 '%s'）\n" "$name" "$url" "$body" "$expect"
        FAIL=1
    fi
}

echo "============================================"
echo " ops-monitor-stack 组件自检"
echo "============================================"

check "prometheus"     "http://localhost:9090/-/ready"
check "alertmanager"   "http://localhost:9093/-/ready"
check "grafana"        "http://localhost:3000/api/health"
check "uptime-kuma"    "http://localhost:3001/"
check "alert-relay"    "http://localhost:8088/healthz"
body_check "nginx"     "http://localhost:8080/healthz" "ok"

echo
echo "--- 抓取目标状态 ---"
targets=$(curl -s --max-time 5 "http://localhost:9090/api/v1/targets?state=active" 2>/dev/null)
if [ -z "$targets" ]; then
    echo "  [FAIL] 无法读取 Prometheus targets"
    FAIL=1
else
    down=$(printf '%s' "$targets" | grep -o '"health":"down"' | wc -l | tr -d ' ')
    up=$(printf '%s' "$targets" | grep -o '"health":"up"' | wc -l | tr -d ' ')
    echo "  up: ${up:-0}   down: ${down:-0}"
    if [ "${down:-0}" != "0" ]; then
        echo "  [FAIL] 存在抓取失败的目标，打开 http://localhost:9090/targets 查看原因"
        FAIL=1
    fi
fi

echo
if [ "$FAIL" -eq 0 ]; then
    echo "结果: 通过"
    exit 0
fi
echo "结果: 失败，见上文 FAIL 行"
exit 1
