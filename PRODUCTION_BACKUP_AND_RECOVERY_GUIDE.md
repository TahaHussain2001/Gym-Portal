# STHX Technologies Gym Portal - Production Backup & Recovery Strategy

## 1. Overview & SLA Requirements

| Metric | Target | Explanation |
| :--- | :--- | :--- |
| **Recovery Point Objective (RPO)** | **< 15 minutes** | Maximum allowable data loss in event of hardware failure. |
| **Recovery Time Objective (RTO)** | **< 30 minutes** | Maximum allowable service outage duration during disaster recovery. |
| **Backup Retention** | **30 Days Daily, 12 Months Monthly** | Compliance retention requirement for financial payments & audit trails. |

---

## 2. Automated Backup Strategy

### A. Full Daily Database Backup (`pg_dump`)
Executes daily at **02:00 AM UTC** using PostgreSQL `pg_dump` with custom directory format (`-F d`), parallel compression (`-j 4`), and SHA-256 checksum generation.

#### Cron Configuration (`/etc/cron.d/sthx_db_backup`)
```bash
0 2 * * * postgres /opt/sthx/scripts/backup_postgres.sh >> /var/log/sthx/backup.log 2>&1
```

#### Production Backup Shell Script (`/opt/sthx/scripts/backup_postgres.sh`)
```bash
#!/usr/bin/env bash
set -euo pipefail

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="/var/backups/sthx_db"
S3_BUCKET="s3://sthx-technologies-backups/postgres"
DB_NAME="sthx_gym_db"
DB_USER="sthx_app_user"

mkdir -p "${BACKUP_DIR}"

BACKUP_FILE="${BACKUP_DIR}/sthx_gym_${TIMESTAMP}.dump"

echo "[$(date)] Starting PostgreSQL backup for ${DB_NAME}..."
pg_dump -h localhost -U "${DB_USER}" -d "${DB_NAME}" -F c -b -v -f "${BACKUP_FILE}"

# Generate SHA-256 Checksum
sha256sum "${BACKUP_FILE}" > "${BACKUP_FILE}.sha256"

# Encrypt backup payload with GPG
gpg --batch --yes --encrypt --recipient ops@sthxtechnologies.com "${BACKUP_FILE}"

# Sync encrypted backup to offsite AWS S3 immutable bucket
aws s3 cp "${BACKUP_FILE}.gpg" "${S3_BUCKET}/${TIMESTAMP}/" --storage-class STANDARD_IA
aws s3 cp "${BACKUP_FILE}.sha256" "${S3_BUCKET}/${TIMESTAMP}/"

# Purge local backups older than 7 days
find "${BACKUP_DIR}" -type f -mtime +7 -delete

echo "[$(date)] Backup completed successfully."
```

---

## 3. Database Restore & Recovery Procedure

### A. Full Database Restore Procedure

1. **Download Backup File and Checksum from S3**:
   ```bash
   aws s3 cp s3://sthx-technologies-backups/postgres/20260725_020000/sthx_gym_20260725_020000.dump.gpg .
   aws s3 cp s3://sthx-technologies-backups/postgres/20260725_020000/sthx_gym_20260725_020000.dump.sha256 .
   ```

2. **Decrypt Backup File**:
   ```bash
   gpg --batch --yes --decrypt --output sthx_gym.dump sthx_gym_20260725_020000.dump.gpg
   ```

3. **Verify SHA-256 Integrity Checksum**:
   ```bash
   sha256sum -c sthx_gym_20260725_020000.dump.sha256
   ```

4. **Restore Database schema and records**:
   ```bash
   # Terminate active backend database connections
   psql -U postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'sthx_gym_db' AND pid <> pg_backend_pid();"

   # Drop and recreate clean database target
   dropdb -U postgres sthx_gym_db
   createdb -U postgres -O sthx_app_user sthx_gym_db

   # Restore using parallel pg_restore
   pg_restore -h localhost -U sthx_app_user -d sthx_gym_db -v --clean --if-exists -j 4 sthx_gym.dump
   ```

---

## 4. Point-In-Time Recovery (PITR) & Emergency Failover

### A. WAL Archiving Setup (`postgresql.conf`)
```ini
wal_level = replica
archive_mode = on
archive_command = 'test ! -f /var/lib/postgresql/wal_archive/%f && cp %p /var/lib/postgresql/wal_archive/%f'
max_wal_senders = 5
```

### B. PITR Recovery Execution
1. Stop PostgreSQL service:
   ```bash
   sudo systemctl stop postgresql
   ```
2. Restore base backup archive to data directory `/var/lib/postgresql/main`.
3. Create `/var/lib/postgresql/main/recovery.signal` trigger file.
4. Add recovery target time in `postgresql.conf`:
   ```ini
   restore_command = 'cp /var/lib/postgresql/wal_archive/%f %p'
   recovery_target_time = '2026-07-25 11:45:00 UTC'
   recovery_target_action = 'promote'
   ```
5. Start PostgreSQL service:
   ```bash
   sudo systemctl start postgresql
   ```
6. Verify database readiness and inspect audit logs:
   ```bash
   curl -f http://127.0.0.1:8000/api/readiness
   ```
