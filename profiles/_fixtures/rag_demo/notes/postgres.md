# Scaling write-heavy Postgres

Partition the table by time so autovacuum works on bounded chunks. Batch the writes and use
COPY over per-row INSERT. Move read traffic to a replica before you reach for sharding.
