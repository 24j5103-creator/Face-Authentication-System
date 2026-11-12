# 勤務状態を管理し、打刻をCSVへ追記します。起動時に状態を復元します。
import csv
import os
from datetime import datetime

class AttendanceManager:
    def __init__(self, users, csv_path):
        self.csv_path = csv_path
        self.status = {user: "未出勤" for user in users}
        self.last_dates = {user: None for user in users}
        folder = os.path.dirname(csv_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        needs_header = not os.path.exists(csv_path)
        if not needs_header:
            # A UTF-8 BOM or blank lines alone are also an empty CSV.
            with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
                needs_header = not any(line.strip() for line in f)
        if needs_header:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(["名前", "日時", "種別"])
        self._restore_status()

    # CSVの最終記録を読み、利用者ごとの勤務状態を復元します。
    def _restore_status(self):
        states = {"出勤": "勤務中", "退勤": "退勤済み", "休憩開始": "休憩中", "休憩終了": "勤務中"}
        with open(self.csv_path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != ["名前", "日時", "種別"]:
                raise ValueError("打刻CSVのヘッダーは「名前,日時,種別」である必要があります。")
            for row in reader:
                name = row["名前"]
                # Keep former users' records in the CSV, but restore current users only.
                if name not in self.status:
                    continue
                try:
                    recorded_at = datetime.strptime(row["日時"], "%Y/%m/%d %H:%M:%S")
                    state = states[row["種別"]]
                    if None in row:
                        raise ValueError("Unexpected CSV column")
                except (TypeError, ValueError, KeyError) as error:
                    raise ValueError(f"打刻CSVの{reader.line_num}行目を読み込めません。") from error
                # CSV records are appended in event order; the last entry determines state.
                self.status[name] = state
                self.last_dates[name] = recorded_at.date()
        for name in self.status:
            self.get_status(name)

    def work_stamp(self, name):
        current_status = self.get_status(name)
        if current_status == "未出勤":
            return self._stamp(name, "出勤", "勤務中")
        if current_status == "勤務中":
            return self._stamp(name, "退勤", "退勤済み")
        print(name, "は現在", current_status, "です。")
        return None

    def break_stamp(self, name):
        current_status = self.get_status(name)
        if current_status == "勤務中":
            return self._stamp(name, "休憩開始", "休憩中")
        if current_status == "休憩中":
            return self._stamp(name, "休憩終了", "勤務中")
        print(name, "は現在", current_status, "です。")
        return None

    def _stamp(self, name, action, next_status):
        # Commit the CSV record before changing in-memory state.
        time_text = self.save(name, action)
        self.status[name] = next_status
        self.last_dates[name] = datetime.strptime(time_text, "%Y/%m/%d %H:%M:%S").date()
        print(name, action, time_text)
        return action

    # サーバーPCの現在時刻でCSVへ追記します。
    def save(self, name, action):
        time_text = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow([name, time_text, action])
        return time_text

    def get_status(self, name):
        # Carry unfinished shifts across midnight; reset only a previously closed shift.
        if self.status[name] == "退勤済み" and self.last_dates[name] < datetime.now().date():
            self.status[name] = "未出勤"
        return self.status[name]
