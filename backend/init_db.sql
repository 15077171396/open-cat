-- AI 猫平台 - 数据库初始化脚本
-- 手动执行: mysql -u root -proot < init_db.sql

CREATE DATABASE IF NOT EXISTS ai_cat
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE ai_cat;
