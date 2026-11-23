.PHONY: up down ps logs

up:
	docker compose up --build

down:
	docker compose down -v

ps:
	docker compose ps

logs:
	docker compose logs -f
