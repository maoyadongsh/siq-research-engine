\set ON_ERROR_STOP on

WITH required_databases(database_name) AS (
    VALUES
        ('siq_app'),
        ('siq_document_parser'),
        ('siq_us'),
        ('siq_hk'),
        ('siq_jp'),
        ('siq_kr'),
        ('siq_eu')
)
SELECT format('CREATE DATABASE %I', database_name)
FROM required_databases
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_database
    WHERE pg_database.datname = required_databases.database_name
)\gexec
