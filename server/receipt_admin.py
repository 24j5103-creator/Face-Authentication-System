# サーバー停止後、CSVを確認したうえで結果不明の要求を整理する管理ツールです。
"""Manual recovery only, after checking attendance.csv. Does not edit attendance CSV."""
import argparse
import json
import sqlite3
from contextlib import closing
from config import BASE_DIR
from instance_lock import instance_lock
from remote_service import operation_id


# 起動引数を読み、必要な処理を初期化してメインループを開始します。
def main():
    parser = argparse.ArgumentParser(description="Inspect/close uncertain receipts with the server STOPPED")
    parser.add_argument("--resolve", help="Request UUID, after manually checking attendance.csv")
    parser.add_argument("--confirmed-reviewed-csv", action="store_true")
    args = parser.parse_args()
    if args.resolve and not args.confirmed_reviewed_csv:
        parser.error("Check attendance.csv, then pass --confirmed-reviewed-csv")
    with instance_lock(BASE_DIR / "data" / "remote_server.lock"):
        path = BASE_DIR / "data" / "remote_requests.sqlite3"
        if not path.exists():
            parser.error("Start the server once to initialize receipts")
        with closing(sqlite3.connect(path)) as db, db:
            if not args.resolve:
                for row in db.execute("SELECT id FROM requests WHERE result IS NULL"):
                    print(row[0], "needs CSV review")
                return
            request_id = operation_id(args.resolve)
            row = db.execute("SELECT result FROM requests WHERE id=?", (request_id,)).fetchone()
            if row and row[0] is not None:
                print("Already completed; no changes made.")
                return
            result = json.dumps({"request_id": request_id, "pending": False, "action": None,
                                 "message": "管理者がCSVを確認し、結果不明の要求を終了しました。勤務状態はCSVを確認してください。"}, ensure_ascii=False)
            if row:
                db.execute("UPDATE requests SET result=? WHERE id=?", (result, request_id))
            else:
                db.execute("INSERT INTO requests VALUES (?, 'manual-resolution', ?)", (request_id, result))
            print("Receipt closed. Attendance CSV was not changed.")


if __name__ == "__main__":
    main()
