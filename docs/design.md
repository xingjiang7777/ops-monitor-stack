# 设计说明

## 1. 为什么用 Docker Compose 而不是 k8s

7 个容器、单机、目标是"能讲清楚每个组件为什么在这儿"。k8s 会引入 etcd、CNI、Ingress 等一堆与监控本身无关的复杂度，排查问题时反而更难定位。Compose 一个文件就能描述全部依赖关系，适合做教学与课程实践。

## 2. 组件职责划分

| 组件 | 职责 | 不负责 |
| --- | --- | --- |
| node-exporter | 暴露主机指标（CPU/内存/磁盘/网络） | 不做判断 |
| Prometheus | 抓取 + 存时序 + 按规则求值 | 不负责通知 |
| Alertmanager | 分组、抑制、静默、路由 | 不组装消息格式 |
| alert-relay | 把 Alertmanager 的 JSON 转成飞书卡片、去重 | 不做告警判定 |
| Grafana | 可视化 | 不做告警（本栈刻意不用 Grafana Alerting） |
| Uptime Kuma | 黑盒探测（HTTP 状态、响应时间、证书） | 不做指标存储 |
| Nginx | 提供静态站点、健康检查端点 | — |

**一个刻意的选择**：告警只在 Prometheus 侧定义，Grafana 只做展示。两处都定义规则会导致"同一个故障收到两条告警"，且排查时不知道哪条生效。Grafana Alerting 能力更强（支持多数据源），但单 Prometheus 场景下没必要。

## 3. 抓取间隔 15s 的取舍

- 15s 是 Prometheus 生态的事实标准，`rate(...[5m])` 这类查询在这套参数下最稳；
- 降到 5s 会让 TSDB 体积翻三倍，而本栈单机 7 个容器，15s 足够捕捉到秒级抖动之外的异常；
- 升到 60s 会让 `DiskWillFillIn4Hours` 这类基于斜率的规则样本变少，预测波动明显变大。

## 4. 告警阈值的依据

| 规则 | 阈值 | 为什么是这个值 |
| --- | --- | --- |
| DiskWillFillIn4Hours | 4 小时内归零 | 清理动作需要人工介入，留出下班/半夜的响应窗口 |
| DiskUsageHigh | 85% | 兜底"突增型"写入，预测规则对此不敏感 |
| MemoryUsageHigh | 90% | 留 10% 给内核与突发；低于 85% 误报太多 |
| InstanceDown | 持续 2m | 容器重启通常几十秒完成，2m 可过滤掉滚动重启 |
| NodeRebooted | 10m 内 | 区分"计划内维护"与"异常重启" |

`for` 的取值比阈值本身更影响误报率。所有规则都留了 `for`，没有一条是"一碰就响"。

## 5. 为什么要自己写告警中继

飞书群机器人可以直接被 Alertmanager 调用吗？可以，但有两个问题：

1. **格式**：Alertmanager 的 webhook 体是通用 JSON，飞书要的是 `msg_type/card` 结构，直接发过去群里显示的是一坨 JSON；
2. **去重**：Alertmanager 的 `repeat_interval` 是按组的，组内只要有一条告警在 firing，整组每 4 小时重发一次。如果只想对"同一条告警"去重，得在接收端做。

中继只有 200 行、零第三方依赖，把这两件事做掉。`DEDUP_WINDOW` 默认 300s，意味着同一 fingerprint 的同一状态 5 分钟内只推一次——足够过滤掉 Alertmanager 的重试，又不会漏掉真正的状态翻转（firing→resolved 的 key 不同）。

## 6. 为什么没有 blackbox_exporter

黑盒探测（HTTP 状态码、TLS 证书剩余天数）Uptime Kuma 已经覆盖，且它带 UI，配置一个监控项比写一段 `relabel_configs` 快得多。

代价是探测数据不在 Prometheus 里，无法与主机指标做关联查询。如果之后要做"服务不可用 ↔ 主机负载"的联合分析，再补 blackbox_exporter，配置要点：

```yaml
- job_name: blackbox
  metrics_path: /probe
  params:
    module: [http_2xx]
  static_configs:
    - targets: ["http://nginx/healthz"]
  relabel_configs:
    - source_labels: [__address__]
      target_label: __param_target
    - source_labels: [__param_target]
      target_label: instance
    - target_label: __address__
      replacement: blackbox-exporter:9115
```

## 7. 已知限制

- 全部组件单机部署，宿主机挂了监控一起挂（这正是要加"外部探测"的原因）；
- 没有持久化到对象存储，Prometheus 数据保留 15 天，机器重建即丢失；
- 告警中继没有做重试队列，飞书接口抖动时会丢消息（当前用 `rate(alertmanager_notifications_failed_total[5m]) > 0` 反过来监控这件事）；
- 未做 HTTPS，所有服务只监听 localhost 场景，暴露到公网前必须加反向代理与认证。
