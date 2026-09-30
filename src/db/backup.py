"""
Database Backup and Disaster Recovery Utility for AI CRISS.
Supports SQLite database hot-snapshots with integrity verification (PRAGMA integrity_check)
and recovery restoration.
"""

import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, Optional


def create_backup(
    db_path: Optional[str] = None,
    backup_dir: Optional[str] = None
) -> Dict[str, Any]:
    """
    Creates a timestamped snapshot of the SQLite database with PRAGMA integrity verification.
    """
    if db_path is None:
        db_path = "/tmp/ai_criss.db" if os.getenv("VERCEL") else "ai_criss.db"
    if backup_dir is None:
        backup_dir = "/tmp/backups" if os.getenv("VERCEL") else "backups"
    if not os.path.exists(db_path):
        return {
            "status": "ERROR",
            "message": f"Source database '{db_path}' does not exist."
        }

    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_filename = f"ai_criss_backup_{timestamp}.db"
    backup_filepath = os.path.join(backup_dir, backup_filename)

    # Use SQLite online backup API for ACID consistency
    try:
        source_conn = sqlite3.connect(db_path)
        backup_conn = sqlite3.connect(backup_filepath)
        with backup_conn:
            source_conn.backup(backup_conn, pages=100)
        backup_conn.close()
        source_conn.close()

        # Run integrity check on the backup file
        verify_conn = sqlite3.connect(backup_filepath)
        cursor = verify_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        check_result = cursor.fetchone()[0]
        verify_conn.close()

        if check_result != "ok":
            os.remove(backup_filepath)
            return {
                "status": "FAILED",
                "message": f"Backup integrity check failed: {check_result}"
            }

        size_bytes = os.path.getsize(backup_filepath)
        return {
            "status": "SUCCESS",
            "backup_file": backup_filepath,
            "size_bytes": size_bytes,
            "integrity": "VERIFIED_OK",
            "timestamp": timestamp
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e)
        }


def restore_backup(
    backup_filepath: str,
    target_db_path: str = "ai_criss.db"
) -> Dict[str, Any]:
    """
    Restores the database from a verified backup file.
    """
    if not os.path.exists(backup_filepath):
        return {
            "status": "ERROR",
            "message": f"Backup file '{backup_filepath}' does not exist."
        }

    try:
        # Verify integrity of backup before restoring
        verify_conn = sqlite3.connect(backup_filepath)
        cursor = verify_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        check_result = cursor.fetchone()[0]
        verify_conn.close()

        if check_result != "ok":
            return {
                "status": "FAILED",
                "message": f"Cannot restore: Backup integrity check failed with '{check_result}'"
            }

        # Backup current database if it exists as safeguard
        if os.path.exists(target_db_path):
            safeguard_path = f"{target_db_path}.pre_restore_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(target_db_path, safeguard_path)

        # Restore
        shutil.copy2(backup_filepath, target_db_path)
        return {
            "status": "SUCCESS",
            "restored_from": backup_filepath,
            "target": target_db_path,
            "integrity": "VERIFIED_OK"
        }
    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e)
        }
