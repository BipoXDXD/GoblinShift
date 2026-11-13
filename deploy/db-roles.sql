-- Papéis de runtime do banco (ADR 055, proposto; issue #366).
--
-- NÃO roda sozinho: nenhum workflow, Compose, JSON do Coolify, entrypoint, script de deploy ou
-- migration o chama (tests/test_db_roles.py confere). O dono roda à mão, conectado como o papel
-- dono do esquema (o mesmo do `migrate`), na ordem do ADR 055:
--
--   docker exec -i goblinshift-postgres-1 \
--     psql -U goblinshift -d goblinshift -v ON_ERROR_STOP=1 < deploy/db-roles.sql
--
-- Idempotente: rodar de novo reaplica os mesmos atributos e privilégios. Sem senha aqui; depois
-- do script, `\password gs_app` e `\password gs_maintenance` no psql, que mandam só o hash.
--
-- gs_runtime  grupo sem login: lê e escreve as tabelas da aplicação, sem DDL nem atributo especial
-- gs_app      login da api e do worker (herda gs_runtime)
-- gs_maintenance  login da manutenção (herda gs_runtime e apaga do audit_event a retenção vencida)
--
-- O conteúdo do audit_event não muda por nenhum papel de runtime, e só a manutenção apaga dele;
-- o alembic_version é só leitura.

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_tables
    WHERE schemaname = 'public' AND tablename = 'alembic_version' AND tableowner = current_user
  ) THEN
    RAISE EXCEPTION 'rode como o dono do esquema (o papel do migrate), não como %', current_user;
  END IF;
END
$$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gs_runtime') THEN
    CREATE ROLE gs_runtime NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gs_app') THEN
    CREATE ROLE gs_app LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gs_maintenance') THEN
    CREATE ROLE gs_maintenance LOGIN;
  END IF;
END
$$;

ALTER ROLE gs_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE gs_app LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE gs_maintenance LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  NOBYPASSRLS;
GRANT gs_runtime TO gs_app, gs_maintenance;

DO $$
BEGIN
  EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO gs_runtime', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO gs_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gs_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gs_runtime;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON alembic_version FROM gs_runtime;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM gs_runtime;
-- Retenção de 90 dias (1.7): só a manutenção apaga, e nenhum papel de runtime tem UPDATE em
-- coluna alguma do audit_event. Por isso o lote da retenção escolhe as linhas sem
-- `FOR UPDATE` (identity/purge.py), que exigiria UPDATE.
GRANT DELETE ON audit_event TO gs_maintenance;

-- Tabelas e sequências que as próximas migrations criarem com este papel já nascem acessíveis.
-- Uma migration que recriar o audit_event volta a dar UPDATE e DELETE: rode este script de novo.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gs_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO gs_runtime;

COMMIT;
