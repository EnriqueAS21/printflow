import os

from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy

load_dotenv()

# Temporal, para contar consultas en desarrollo: SQL_ECHO=1 equivale a SQLALCHEMY_ECHO=True.
db = SQLAlchemy(engine_options={'echo': True} if os.environ.get('SQL_ECHO') == '1' else None)
