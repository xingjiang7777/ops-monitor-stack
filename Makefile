SHELL := /bin/bash
COMPOSE := docker compose
PROM_IMAGE := prom/prometheus:v2.54.1
AM_IMAGE := prom/alertmanager:v0.27.0

.PHONY: up down clean ps logs verify validate reload simulate simulate-clean

up:
	$(COMPOSE) up -d
	@echo "站点 http://localhost:8080  Grafana http://localhost:3000  Prometheus http://localhost:9090"

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100 $(S)

verify:
	bash scripts/verify_stack.sh

# 用官方镜像里的 promtool / amtool 做静态校验，避免本机装 Go 工具链
validate:
	docker run --rm \
		-v "$(PWD)/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro" \
		-v "$(PWD)/prometheus/rules:/etc/prometheus/rules:ro" \
		--entrypoint promtool $(PROM_IMAGE) check config /etc/prometheus/prometheus.yml
	docker run --rm \
		-v "$(PWD)/alertmanager/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro" \
		--entrypoint amtool $(AM_IMAGE) check-config /etc/alertmanager/alertmanager.yml

reload:
	curl -s -X POST http://localhost:9090/-/reload && echo "prometheus reloaded"

simulate:
	bash scripts/simulate_disk_full.sh $(SIZE)

simulate-clean:
	bash scripts/simulate_disk_full.sh --clean
