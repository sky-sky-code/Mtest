# Архитектура сервиса

Сервис решает задачу выполнения команд на большом парке хостов (≥ 1000) в условиях нестабильной сети (таймауты, обрывы, задержки) с поддержкой **approval** и **host-level** блокировок.

## Компоненты

- **API (FastAPI)** — принимает webhook, валидирует данные, создаёт `Job` и `Execution` и `OutboxEvent` в БД, отвечает быстро (`job_id`).
- **PostgreSQL** — источник истины: хранит `Host`, `HostCommandBlock`, `Job`, `Execution`, `ExecutionLogs`, `Outbox`.
- **Redis** — брокер очередей Celery.
- **Celery Beat** — периодически запускает `publish_outbox`.
- **Celery Worker** — выполняет `plan_job` и `run_execution`.
- **Agent (симуляция)** — имитирует выполнение команд на хостах и нестабильную сеть (ошибки/таймауты/успех).

## Поток обработки (pipeline)

1. `POST /webhook/jobs`
2. В транзакции создаются `Job` и связанные `Execution` (по выбранным хостам).
3. Если approval не требуется, создаётся Outbox-событие `PLAN_JOB`.
4. `publish_outbox` читает `Outbox` и публикует задачу `plan_job(job_id)` в Celery.
5. `plan_job` батчами переводит `Execution` `NEW -> QUEUED` и отправляет `run_execution(execution_id)` в очередь.
6. `run_execution` выполняет команду через agent, пишет логи, фиксирует итоговый статус.

---

# Ключевые решения

## 1) Idempotency (дедуп webhook)

Цель: повторная доставка одного и того же webhook не должна создавать дубликаты.

- Webhook содержит `external_id`.
- В БД задан уникальный ключ: `UNIQUE(external_id)`.
- Создание `Job` делается через проверку существования job c external_id `SELECT * FORM job WHERE external_id = id`:
  - если job  уже существует, возвращаем существующий `job_id`
  - если job отсутствует → создаем `job`

Таким образом, повторный webhook возвращает тот же `job_id` и не создаёт дубликаты.

## 2) Approval workflow

Некоторые `command_type` требуют ручного подтверждения.

При создании `Job`:
- если approval нужен → `job.approval_state = WAIT_APPROVAL`, в очередь не планируем
- если approval не нужен → планируем сразу через Outbox

Endpoints:
- `POST /jobs/{job_id}/approve`
  - переводит `approval_state = APPROVED`
  - создаёт Outbox-событие `PLAN_JOB`
- `POST /jobs/{job_id}/reject`
  - переводит `approval_state = REJECTED`
  - отменяет ещё не начатые executions (`NEW/QUEUED -> CANCELLED`)

`plan_job` дополнительно защищён от обхода approval: планирует только если  
`approval_state IS NULL` или `approval_state == APPROVED`.

## 3) Host-level блокировки

Таблица `HostCommandBlock(host_id, command_type)` задаёт запреты выполнения определённых типов команд на конкретном хосте.

Выбранное поведение для MVP:
- `run_execution` перед запуском проверяет блокировку по `(host_id, command_type)`
- при наличии блокировки выставляет `Execution.status = BLOCKED` и не запускает выполнение

## 4) Конкурентность и блокировки (Locks)

Требование: нельзя допустить параллельного выполнения несовместимых операций на одном хосте (минимум — один execution на host одновременно).

Используются два уровня защиты:

### 4.1 Optimistic concurrency на уровне Execution

Защищает от ситуации, когда два воркера одновременно “забрали” один и тот же execution.

В `run_execution` используется атомарный переход:

```sql
UPDATE executions
SET status = 'RUNNING'
WHERE id = :id AND status = 'QUEUED';
```

### 4.2 Postgres advisory lock на уровне Host

Postgres advisory lock на уровне Host
- Перед выполнением берётся lock:
  - `SELECT pg_try_advisory_lock(hash(host_id))`
- Если lock не получен — хост занят, выполняется retry с backoff.
- Lock удерживается на одном DB-соединении на время agent-call и снимается:
  - `SELECT pg_advisory_unlock(hash(host_id))`

## 5 Retries / backoff / timeouts

Сеть и агент нестабильны, поэтому выполнение поддерживает повторные попытки.
- run_execution использует Celery retry (self.retry), лимит EXEC_MAX_RETRIES.
- Задержка перед повтором: exponential backoff + jitter 
- delay = min(MAX_BACKOFF, BASE_BACKOFF * 2^retries_done) + random(0..1)

Поведение по ошибкам:
- TimeoutError → retry до лимита, затем Execution.status = TIMEOUT
- другие ошибки → retry до лимита, затем Execution.status = FAILED/FAILURE

Это снижает нагрузку при массовых сбоях и повышает шанс успешного выполнения при временных проблемах.