-- Schema do Radar Bancário.
-- Modelagem em duas tabelas: metadados (series) e fatos (observacoes),
-- com unicidade por (serie_id, data) para garantir cargas idempotentes.

CREATE TABLE IF NOT EXISTS series (
    id           INTEGER PRIMARY KEY,
    codigo_sgs   INTEGER NOT NULL UNIQUE,
    slug         TEXT    NOT NULL UNIQUE,
    nome         TEXT    NOT NULL,
    unidade      TEXT    NOT NULL,
    frequencia   TEXT    NOT NULL CHECK (frequencia IN ('diaria', 'mensal')),
    data_inicial TEXT    NOT NULL,
    criado_em    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS observacoes (
    id          INTEGER PRIMARY KEY,
    serie_id    INTEGER NOT NULL REFERENCES series (id) ON DELETE CASCADE,
    data        TEXT    NOT NULL,           -- ISO: YYYY-MM-DD
    valor       REAL    NOT NULL,
    coletado_em TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (serie_id, data)
);

CREATE INDEX IF NOT EXISTS idx_observacoes_serie_data
    ON observacoes (serie_id, data);
