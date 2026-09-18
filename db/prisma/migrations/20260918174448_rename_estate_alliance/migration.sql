-- The second estate is Alliance, not "accounting". The original name described
-- the shape of its export rather than the business it belongs to, which made
-- every label on the dashboard wrong for the reader.
--
-- Renamed in place rather than recreated: ALTER TYPE ... RENAME VALUE keeps
-- every existing row, so 1.2M loaded transactions are untouched.

ALTER TYPE "Estate" RENAME VALUE 'ACCOUNTING' TO 'ALLIANCE';

UPDATE "transaction" SET feed = 'alliance_line' WHERE feed = 'accounting_line';
UPDATE "transaction" SET feed = 'alliance_sale' WHERE feed = 'accounting_sale';
