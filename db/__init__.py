import os
from sqlmodel import create_engine, SQLModel

# Читаємо шлях до папки з даними (за замовчуванням - поточна папка)
DATA_DIR = os.getenv("DATA_DIR", ".")
sqlite_file_name = os.path.join(DATA_DIR, "data.db")
sqlite_url = f"sqlite:///{sqlite_file_name}"

engine = create_engine(sqlite_url)

def init():
    SQLModel.metadata.create_all(engine)
