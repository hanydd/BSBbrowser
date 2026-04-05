#!/bin/bash

# Same-machine mirror sync (no scp/ssh): see export/sync_same_server.sh

# ===========================================
# PostgreSQL 数据导出工具
# ===========================================

# 数据库配置
SOURCE_DB="sponsorTimes"
TARGET_DB="sponsorblock"
DB_USER="postgres"
DB_PASSWORD="postgres"
OUTPUT_FILE="sponsorTimes.sql"
COMPRESSED_FILE="sponsorTimes.sql.xz"
TIMESTAMP=$(date -u +"%Y-%m-%d %H:%M:%S")

# 远程服务器配置
REMOTE_USER="ecs-user"
REMOTE_HOST="47.103.74.95"
REMOTE_PATH="~/sync/"
POPULATE_SCRIPT="~/sync/populate.sh"

# 设置密码环境变量（避免密码提示）
export PGPASSWORD="$DB_PASSWORD"

echo "========================================="
echo "PostgreSQL 数据导出工具"
echo "源数据库: $SOURCE_DB"
echo "目标数据库: $TARGET_DB"
echo "用户: $DB_USER"
echo "导出时间: $TIMESTAMP UTC"
echo "执行用户: hanydd"
echo "========================================="

cd /home/ecs/sync

# 检查源数据库是否存在
echo "检查源数据库连接..."
psql -U $DB_USER -d $SOURCE_DB -c "\q" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "错误: 无法连接到源数据库 $SOURCE_DB"
    echo "请检查数据库名称、用户名和密码是否正确"
    exit 1
fi

# 获取表列表（排除videoInfo表）
echo "获取表列表..."
TABLES=$(psql -U $DB_USER -d $SOURCE_DB -t -c "
    SELECT tablename
    FROM pg_tables
    WHERE schemaname = 'public'
    AND tablename != 'videoInfo'
    ORDER BY tablename;
" | grep -v '^$')

echo "找到以下表（已排除videoInfo表）:"
echo "$TABLES"
echo ""

# 检查是否有表可以导出
if [ -z "$TABLES" ]; then
    echo "警告: 没有找到可导出的表"
    exit 1
fi

# 导出数据
echo "开始导出数据..."
echo "-- 数据导出文件" > "$OUTPUT_FILE"
echo "-- 源数据库: $SOURCE_DB" >> "$OUTPUT_FILE"
echo "-- 导出时间: $TIMESTAMP UTC" >> "$OUTPUT_FILE"
echo "-- 执行用户: hanydd" >> "$OUTPUT_FILE"
echo "-- 注意: 已排除 videoInfo 表" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

# 使用pg_dump导出（排除videoInfo表）
pg_dump -U $DB_USER \
    --data-only \
    --no-owner \
    --no-privileges \
    --disable-triggers \
    --exclude-table=videoInfo \
    $SOURCE_DB > "$OUTPUT_FILE"

# 检查导出结果
if [ $? -eq 0 ]; then
    echo "数据导出成功，正在添加config表记录..."

    # 添加config表的INSERT语句
    echo "" >> "$OUTPUT_FILE"
    echo "-- 添加config表更新记录" >> "$OUTPUT_FILE"
    CURRENT_ISO_TIME=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
    echo "INSERT INTO public.config VALUES ('updated', '$CURRENT_ISO_TIME');" >> "$OUTPUT_FILE"

    echo "config表记录已添加，时间戳: $CURRENT_ISO_TIME"

    # 获取压缩前文件信息
    FILE_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)
    LINE_COUNT=$(wc -l < "$OUTPUT_FILE")
    INSERT_COUNT=$(grep -c "^INSERT INTO" "$OUTPUT_FILE")

    echo "正在使用xz -9压缩文件..."

    # 使用xz -9进行最高级别压缩
    xz -9 -f "$OUTPUT_FILE"

    if [ $? -eq 0 ]; then
        # 获取压缩后文件信息
        COMPRESSED_SIZE=$(du -h "$COMPRESSED_FILE" | cut -f1)

        echo "========================================="
        echo "导出和压缩完成！"
        echo "原始文件大小: $FILE_SIZE"
        echo "压缩文件大小: $COMPRESSED_SIZE"
        echo "总行数: $LINE_COUNT"
        echo "config更新时间: $CURRENT_ISO_TIME"
        echo "========================================="
        echo ""

        echo "传递文件到远程服务器..."
        scp $COMPRESSED_FILE $REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH

        if [ $? -eq 0 ]; then
            echo "文件传输成功！"
            echo ""
            echo "正在调用远程服务器上的数据导入脚本..."

            # 执行远程服务器上的populate.sh脚本
            ssh $REMOTE_USER@$REMOTE_HOST "cd ~/sync && bash $POPULATE_SCRIPT"

            if [ $? -eq 0 ]; then
                echo "========================================="
                echo "完整流程执行成功！"
                echo "1. 数据导出完成"
                echo "2. 文件压缩完成"
                echo "3. 文件传输完成"
                echo "4. 远程导入脚本执行完成"
                echo "========================================="
                echo ""
                echo "远程数据库已更新，时间戳: $CURRENT_ISO_TIME"
            else
                echo "警告: 远程导入脚本执行失败"
                echo "文件已成功传输到: $REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH$COMPRESSED_FILE"
                echo "请手动检查远程服务器上的populate.sh脚本"
                exit 1
            fi
        else
            echo "文件传输失败！"
            echo "本地压缩文件: $COMPRESSED_FILE"
            echo "请检查网络连接和远程服务器状态"
            exit 1
        fi
    else
        echo "压缩失败！但SQL文件已成功生成: $OUTPUT_FILE"
        exit 1
    fi

else
    echo "导出失败！请检查:"
    exit 1
fi

# 清理密码环境变量
unset PGPASSWORD