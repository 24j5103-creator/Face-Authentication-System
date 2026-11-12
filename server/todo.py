# 認証された利用者に対応するTodoテキストを読み込みます。
import os

class TodoViewer:
    def __init__(self, todo_folder, todo_files=None):
        self.todo_folder = todo_folder
        self.todo_files = dict(todo_files or {})

    def read(self, name):
        if name not in self.todo_files:
            raise ValueError("Unknown Todo user")
        file_path = os.path.join(self.todo_folder, self.todo_files[name])
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def show(self, name):
        file_path = os.path.join(self.todo_folder, self.todo_files.get(name, name + ".txt"))
        
        # Check file
        if not os.path.exists(file_path):
            print(name, "のTodoファイルが見つかりません。")
            return

        # Read Todo file
        with open(file_path, "r", encoding="utf-8") as f:
            todo_text = f.read()
        print("")
        print("====================")
        print(name, "Todo")
        print("====================")
        print(todo_text)
        print("====================")
        print("")
