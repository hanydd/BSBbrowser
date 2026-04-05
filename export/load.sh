#!/bin/bash

# 配置区域
DB_HOST="localhost"
DB_USER="postgres"
DB_PASSWORD="postgres"
BACKUP_DIR="/home/ecs/backup"

export PGPASSWORD="${DB_PASSWORD}"

dropdb -U ${DB_USER} sponsorTimes
dropdb -U ${DB_USER} privateDB

createdb -U ${DB_USER} sponsorTimes
createdb -U ${DB_USER} privateDB

psql  -U ${DB_USER} sponsorTimes < "${BACKUP_DIR}/sponsorTimes.sql"
psql  -U ${DB_USER} privateDB < "${BACKUP_DIR}/privateDB.sql"