# check_url.py
from dotenv import load_dotenv
import os
from sqlalchemy.engine import make_url
load_dotenv()
u = make_url(os.getenv("DATABASE_URL",""))
print("Driver =", u.drivername)         # expect: postgresql+asyncpg
print("Query  =", u.query)              # expect: {'ssl': 'true'} or {}
