# ops-monitor-stack：单机监控告警栈（Docker Compose）

一套可以在笔记本上跑起来的**站点 + 监控 + 告警**闭环：Nginx 托管静态页，Prometheus 采集主机指标并按规则求值，Alertmanager 负责分组抑制，自研的零依赖中继把告警转成飞书卡片并去重，Grafana 做可视化，Uptime Kuma 做黑盒探测。

> 定位：课程实践 / 个人实验环境。7 个容器，`docker compose up -d` 一条命令拉起，不需要 k8s、不需要云资源。

## 1. 架构

```
                    ┌──────────────┐
   browser ────────▶│  nginx :8080 │  静态站点 + /healthz
                    └──────────────┘
                            ▲
                    ┌───────┴────────┐
                    │ uptime-kuma    │  HTTP 探测（状态码/响应时间/证书）
                    │ :3001          │
                    └────────────────┘

  ┌───────────────┐   scrape    ┌──────────────┐   rules    ┌───────────────┐
  │ node-exporter │◀────────────│  prometheus  │───────────▶│ alertmanager  │
  │ :9100         │   15s       │  :9090       │            │ :9093         │
  └───────────────┘             └──────┬───────┘            └───────┬───────┘
                                       │ query                      │ webhook
                                ┌──────▼───────┐            ┌───────▼───────┐
                                │  grafana     │            │  alert-relay  │
                                │  :3000       │            │  :8088        │
                                └──────────────┘            └───────┬───────┘
                                                                    │ card
                                                              ┌─────▼─────┐
                                                              │  飞书群    │
                                                              └───────────┘
```

## 2. 组件与端口

| 服务 | 端口 | 作用 |
| --- | --- | --- |
| nginx | 8080 | 静态站点、`/healthz` 健康检查 |
| prometheus | 9090 | 指标抓取（15s）、规则求值、15 天保留 |
| alertmanager | 9093 | 告警分组、抑制、静默、路由 |
| alert-relay | 8088 | Alertmanager → 飞书卡片，按指纹去重 |
| grafana | 3000 | 主机总览看板（已 provisioning，免手工导入） |
| uptime-kuma | 3001 | 站点可用性探测 |
| node-exporter | 内部 9100 | 主机指标（挂载宿主 `/`，只读） |

## 3. 快速开始

```bash
git clone https://github.com/xingjiang7777/ops-monitor-stack.git
cd ops-monitor-stack
cp .env.example .env        # 填入飞书机器人 webhook（可留空，留空只打日志）
docker compose up -d
bash scripts/verify_stack.sh
```

打开：

- 站点 <http://localhost:8080>
- Grafana <http://localhost:3000>（`admin` / `.env` 里的密码）
- Prometheus <http://localhost:9090/targets>，两个目标都应为 `UP`

首次启动需要拉取镜像（约 600 MB），之后冷启动约 10 秒。

## 4. 告警规则

规则在 `prometheus/rules/host.rules.yml`，共 7 条：

| 规则 | 表达式要点 | `for` | 级别 |
| --- | --- | --- | --- |
| InstanceDown | `up == 0` | 2m | critical |
| DiskWillFillIn4Hours | `predict_linear(avail[6h], 4*3600) < 0` | 10m | warning |
| DiskUsageHigh | 使用率 > 85% | 15m | warning |
| MemoryUsageHigh | 使用率 > 90% | 10m | warning |
| NodeRebooted | `time() - node_boot_time_seconds < 600` | 0 | info |
| PrometheusConfigReloadFailed | `prometheus_config_last_reload_successful == 0` | 5m | warning |
| AlertmanagerNotificationFailing | `rate(alertmanager_notifications_failed_total[5m]) > 0` | 10m | warning |

最后两条是"监控监控自己"——上一版就是因为没人看磁盘指标才出的故障，复盘见 [`docs/incident-disk-full.md`](docs/incident-disk-full.md)。

## 5. 故障演练

```bash
bash scripts/simulate_disk_full.sh 2G     # 写入 2G 文件，制造磁盘增长趋势
# 10~30 分钟后 DiskWillFillIn4Hours 触发，飞书群收到卡片
bash scripts/simulate_disk_full.sh --clean
```

为什么不是立刻触发：`predict_linear` 需要窗口内足够的样本才能算出可信斜率，刚写入时 6 小时窗口里只有几分钟的数据。这个"延迟"本身值得在面试里讲——**基于斜率的告警天然有冷启动期**，所以还需要 85% 阈值做兜底。

想让告警立刻出现，可以直接停一个容器验证链路：

```bash
docker stop ops-node-exporter           # 2 分钟后 InstanceDown 触发
docker start ops-node-exporter          # 收到 resolved 通知
```

## 6. 常用命令

```bash
make up          # 启动全部服务
make ps          # 查看状态
make logs S=prometheus
make verify      # 组件自检 + 抓取目标健康检查
make validate    # promtool 校验规则、amtool 校验 Alertmanager 配置
make reload      # 热加载 Prometheus 配置（改了 rules 不用重启）
make down        # 停止并保留数据卷
make clean       # 停止并删除数据卷（会丢历史数据）
```

## 7. 可选：换一个视角看问题（eBPF）

前面这套（node-exporter + Prometheus）是指标视角：**资源用了多少、有没有越过阈值**。
它答不了另一个问题：**service A 变慢，是 B 拖的，还是 C 拖的？**

[eBPF](https://ebpf.io/) 视角（[coroot/coroot](https://github.com/coroot/coroot)，Apache-2.0）不需要在代码里加埋点，直接从内核抓网络与进程调用，自动生成服务拓扑：

```bash
docker compose --profile ebpf up -d     # 默认 up 不会启动它
# 打开 http://localhost:8085
```

它被放进 `ebpf` profile，是因为有三个硬条件：**内核 ≥ 5.4**、宿主机允许 `privileged` 容器、能看到 `/sys/kernel/debug`。少一个就起不来，所以不该拖累默认路径。

两种视角的分工，也是面试里可以讲的一段：

| | 指标视角（本仓库默认） | eBPF 视角（可选） |
| --- | --- | --- |
| 回答什么 | 资源够不够、有没有越线 | 谁在拖慢谁、调用链长什么样 |
| 成本 | 低：一个 exporter，几 MB | 高：需要特权容器与内核支持，常驻内存几百 MB |
| 盲区 | 不知道请求在服务之间怎么走 | 拿不到"磁盘还剩多少"这类资源事实 |

## 8. 目录结构

```
.
├── docker-compose.yml            # 7 个默认服务 + ebpf profile + 统一日志上限锚点
├── .env.example
├── prometheus/
│   ├── prometheus.yml
│   └── rules/host.rules.yml      # 7 条告警规则
├── alertmanager/alertmanager.yml # 路由、抑制、webhook 接收端
├── relay/                        # 飞书告警中继（标准库，无依赖）
│   ├── app.py
│   └── Dockerfile
├── grafana/
│   ├── provisioning/             # 数据源与看板自动加载
│   └── dashboards/host-overview.json
├── nginx/conf.d/site.conf
├── site/                         # 静态站点
├── scripts/
│   ├── verify_stack.sh
│   └── simulate_disk_full.sh
├── docs/
│   ├── design.md                 # 选型与阈值依据
│   └── incident-disk-full.md     # 磁盘写满故障复盘
└── Makefile
```

## 9. 已知限制

- 单机部署，宿主机宕机则监控同时失效；
- 未配置 HTTPS 与鉴权，**不要直接暴露到公网**；
- 告警中继无重试队列，飞书接口抖动会丢单条消息（已用规则反向监控外发失败率）；
- Prometheus 数据保留 15 天，未接远程存储；ebpf profile 需内核 5.4+ 与特权容器，Windows/macOS 上的 Docker Desktop 默认拿不到 /sys/kernel/debug。

## 10. License

MIT
