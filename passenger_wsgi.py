import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))

if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)
    
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["BLIS_NUM_THREADS"] = "1"

from app import app

application = app